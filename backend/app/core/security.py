"""Security primitives: password hashing, JWT issue/verify, refresh rotation.

This module is deliberately free of FastAPI, ORM, and database imports so it can
be reused by the API layer, background workers, and CLI scripts alike. Higher
layers (``services.auth_service`` / ``api.deps``) translate the errors raised
here into HTTP responses and persist refresh-token hashes into ``user_sessions``.

Design notes (ARCHITECTURE.md §10.2):
- Passwords are hashed with Argon2 (bcrypt kept as a verified legacy scheme).
- Access + refresh tokens are signed JWTs carrying ``jti`` (for the Redis-backed
  denylist / session ledger), ``type`` (access|refresh), issuer, and audience.
- Refresh tokens are additionally stored server-side only as a SHA-256 hash
  (``user_sessions.refresh_token_hash``) enabling rotation and reuse detection.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.constants import TokenType

# Argon2 is the default hasher; bcrypt remains verifiable for migrated hashes.
_pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


class SecurityError(Exception):
    """Base class for security failures raised by this module."""


class TokenError(SecurityError):
    """Raised when a JWT is malformed, expired, or fails validation."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted token plus the metadata callers must persist/track."""

    token: str
    jti: str
    token_type: TokenType
    expires_at: datetime
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class DecodedToken:
    """Validated JWT claims in a typed, convenient form."""

    subject: str
    token_type: TokenType
    jti: str
    org_id: str | None
    role: str | None
    issued_at: datetime
    expires_at: datetime
    claims: dict[str, Any]


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Return an Argon2 hash of ``password``."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verify ``plain_password`` against a stored hash."""
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        # Malformed/unknown hash: treat as a failed verification, never raise.
        return False


def password_needs_rehash(hashed_password: str) -> bool:
    """True when a stored hash uses a deprecated scheme/params and should rotate."""
    return _pwd_context.needs_update(hashed_password)


# --------------------------------------------------------------------------- #
# Token identifiers
# --------------------------------------------------------------------------- #
def generate_jti() -> str:
    """Generate a unique JWT id (used for denylisting / session tracking)."""
    return uuid.uuid4().hex


def generate_refresh_secret() -> str:
    """High-entropy opaque secret embedded in a refresh token for rotation."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Deterministic SHA-256 of a refresh token for the ``user_sessions`` ledger."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# JWT issue / verify
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(UTC)


def _encode(
    *,
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None,
) -> IssuedToken:
    settings = get_settings()
    issued_at = _now()
    expires_at = issued_at + expires_delta
    jti = generate_jti()

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "jti": jti,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    if extra_claims:
        payload.update({k: v for k, v in extra_claims.items() if v is not None})

    token = jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    return IssuedToken(
        token=token,
        jti=jti,
        token_type=token_type,
        expires_at=expires_at,
        issued_at=issued_at,
    )


def create_access_token(
    subject: str,
    *,
    org_id: str | None = None,
    role: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> IssuedToken:
    """Issue a short-lived access token (identity always derived from this)."""
    settings = get_settings()
    claims: dict[str, Any] = {"org_id": org_id, "role": role}
    if extra_claims:
        claims.update(extra_claims)
    return _encode(
        subject=subject,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims=claims,
    )


def create_refresh_token(
    subject: str,
    *,
    org_id: str | None = None,
    role: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> IssuedToken:
    """Issue a long-lived refresh token (rotated + hashed in ``user_sessions``)."""
    settings = get_settings()
    claims: dict[str, Any] = {"org_id": org_id, "role": role}
    if extra_claims:
        claims.update(extra_claims)
    return _encode(
        subject=subject,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        extra_claims=claims,
    )


def decode_token(
    token: str,
    *,
    expected_type: TokenType | None = None,
) -> DecodedToken:
    """Validate ``token`` (signature, exp/nbf/iat, issuer, audience) and decode it.

    Raises :class:`TokenError` on any validation failure.
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid.") from exc

    raw_type = payload.get("type")
    try:
        token_type = TokenType(raw_type)
    except ValueError as exc:
        raise TokenError("Token has an unknown type.") from exc

    if expected_type is not None and token_type != expected_type:
        raise TokenError(f"Expected a {expected_type.value} token but received {token_type.value}.")

    return DecodedToken(
        subject=str(payload["sub"]),
        token_type=token_type,
        jti=str(payload["jti"]),
        org_id=payload.get("org_id"),
        role=payload.get("role"),
        issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        claims=payload,
    )


__all__ = [
    "SecurityError",
    "TokenError",
    "IssuedToken",
    "DecodedToken",
    "hash_password",
    "verify_password",
    "password_needs_rehash",
    "generate_jti",
    "generate_refresh_secret",
    "hash_refresh_token",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]
