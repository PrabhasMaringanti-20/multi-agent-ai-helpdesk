"""Data access for users and user sessions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import User, UserSession
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, org_id: uuid.UUID, email: str) -> User | None:
        """Case-insensitive email lookup within a tenant (email is CITEXT)."""
        stmt = (
            select(User)
            .where(User.org_id == org_id, User.email == email)
            .options(selectinload(User.role))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_email(self, org_id: uuid.UUID, email: str) -> User | None:
        stmt = (
            select(User)
            .where(
                User.org_id == org_id,
                User.email == email,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .options(selectinload(User.role))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_role(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.role)).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def email_exists(self, org_id: uuid.UUID, email: str) -> bool:
        return await self.exists(org_id=org_id, email=email)

    async def mark_logged_in(self, user: User) -> User:
        user.last_login_at = datetime.now(UTC)
        await self.session.flush()
        return user


class UserSessionRepository(BaseRepository[UserSession]):
    model = UserSession

    async def get_active_by_hash(self, token_hash: str) -> UserSession | None:
        stmt = (
            select(UserSession)
            .where(
                UserSession.refresh_token_hash == token_hash,
                UserSession.revoked_at.is_(None),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_for_user(self, user_id: uuid.UUID) -> Sequence[UserSession]:
        stmt = select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def revoke(self, session_row: UserSession) -> UserSession:
        session_row.revoked_at = datetime.now(UTC)
        await self.session.flush()
        return session_row

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        rows = await self.list_active_for_user(user_id)
        now = datetime.now(UTC)
        for row in rows:
            row.revoked_at = now
        await self.session.flush()
        return len(rows)


__all__ = ["UserRepository", "UserSessionRepository"]
