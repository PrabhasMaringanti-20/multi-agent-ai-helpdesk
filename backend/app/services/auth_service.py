"""Authentication business logic.

Owns registration, credential verification, JWT issuance, refresh-token rotation
with reuse detection, and logout. Transaction boundaries are owned by the
request-scoped session (``get_session`` commits on success); this service
flushes through repositories and writes audit entries for security events.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ActorType, RoleKey
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
)
from app.core.security import (
    IssuedToken,
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.models.organization import Organization, Role
from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserRepository, UserSessionRepository
from app.schemas.auth import RegisterRequest


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        sessions: UserSessionRepository,
        audit: AuditRepository,
    ) -> None:
        self.session = session
        self.users = users
        self.sessions = sessions
        self.audit = audit

    # ------------------------------------------------------------------ #
    # Lookup helpers (roles + organizations are seed/lookup tables)
    # ------------------------------------------------------------------ #
    async def _get_org_by_slug(self, slug: str) -> Organization:
        stmt = select(Organization).where(
            Organization.slug == slug, Organization.is_active.is_(True)
        )
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if org is None:
            raise NotFoundError("Organization not found or inactive.")
        return org

    async def _get_role_by_key(self, key: str) -> Role:
        stmt = select(Role).where(Role.key == key)
        result = await self.session.execute(stmt)
        role = result.scalar_one_or_none()
        if role is None:
            raise NotFoundError(f"Role '{key}' is not seeded.")
        return role

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    async def register(self, data: RegisterRequest) -> User:
        org = await self._get_org_by_slug(data.org_slug)
        if await self.users.email_exists(org.id, data.email):
            raise ConflictError("An account with this email already exists.")

        role = await self._get_role_by_key(RoleKey.END_USER.value)
        user = await self.users.create(
            org_id=org.id,
            email=data.email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
            role_id=role.id,
            locale=data.locale,
            is_active=True,
        )
        await self.audit.record(
            org_id=org.id,
            action="auth.register",
            resource_type="user",
            actor_type=ActorType.USER,
            actor_user_id=user.id,
            resource_id=user.id,
        )
        # Reload with the role relationship for principal/response construction.
        loaded = await self.users.get_with_role(user.id)
        assert loaded is not None  # just created within this transaction
        return loaded

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #
    async def authenticate(self, org_slug: str, email: str, password: str) -> User:
        org = await self._get_org_by_slug(org_slug)
        user = await self.users.get_active_by_email(org.id, email)
        # Verify even when the user is missing to reduce timing side channels.
        stored_hash = user.hashed_password if user is not None else _DUMMY_HASH
        if not verify_password(password, stored_hash) or user is None:
            raise AuthenticationError("Invalid email or password.")

        if password_needs_rehash(user.hashed_password):
            user.hashed_password = hash_password(password)
        await self.users.mark_logged_in(user)
        await self.audit.record(
            org_id=org.id,
            action="auth.login",
            resource_type="user",
            actor_type=ActorType.USER,
            actor_user_id=user.id,
            resource_id=user.id,
        )
        return user

    # ------------------------------------------------------------------ #
    # Token issuance / rotation
    # ------------------------------------------------------------------ #
    async def issue_token_pair(
        self,
        user: User,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[IssuedToken, IssuedToken]:
        role_key = user.role.key
        access = create_access_token(str(user.id), org_id=str(user.org_id), role=role_key)
        refresh = create_refresh_token(str(user.id), org_id=str(user.org_id), role=role_key)
        await self.sessions.create(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh.token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=refresh.expires_at,
        )
        return access, refresh

    async def refresh_tokens(
        self,
        refresh_token_value: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[IssuedToken, IssuedToken]:
        from app.core.constants import TokenType
        from app.core.security import TokenError, decode_token

        try:
            decoded = decode_token(refresh_token_value, expected_type=TokenType.REFRESH)
        except TokenError as exc:
            raise AuthenticationError(str(exc)) from exc

        token_hash = hash_refresh_token(refresh_token_value)
        session_row = await self.sessions.get_active_by_hash(token_hash)

        if session_row is None:
            # Valid signature but no active session for this hash => the token was
            # already rotated/revoked. Treat as reuse: revoke all of the subject's
            # sessions defensively.
            with contextlib.suppress(ValueError, TypeError):
                await self.sessions.revoke_all_for_user(uuid.UUID(decoded.subject))
            raise AuthenticationError("Refresh token is no longer valid.")

        if session_row.expires_at <= datetime.now(UTC):
            await self.sessions.revoke(session_row)
            raise AuthenticationError("Refresh token has expired.")

        # Rotate: revoke the presented session, issue a fresh pair.
        await self.sessions.revoke(session_row)
        user = await self.users.get_with_role(session_row.user_id)
        if user is None or not user.is_active or user.deleted_at is not None:
            raise AuthenticationError("The account is inactive.")

        return await self.issue_token_pair(user, user_agent=user_agent, ip_address=ip_address)

    # ------------------------------------------------------------------ #
    # Logout
    # ------------------------------------------------------------------ #
    async def logout(
        self,
        refresh_token_value: str,
        *,
        actor_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
    ) -> None:
        token_hash = hash_refresh_token(refresh_token_value)
        session_row = await self.sessions.get_active_by_hash(token_hash)
        if session_row is not None:
            await self.sessions.revoke(session_row)
            if org_id is not None:
                await self.audit.record(
                    org_id=org_id,
                    action="auth.logout",
                    resource_type="user_session",
                    actor_type=ActorType.USER,
                    actor_user_id=actor_id,
                    resource_id=session_row.id,
                )


# Precomputed dummy Argon2 hash used to equalize the "user not found" path.
_DUMMY_HASH = hash_password("dummy-password-for-timing-equalization")


__all__ = ["AuthService"]
