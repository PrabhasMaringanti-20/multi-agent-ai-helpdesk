"""Authentication request/response DTOs and the internal request Principal.

The multi-tenant design keys users on ``(org_id, email)`` (email is unique per
organization), so login and registration carry an ``org_slug`` to resolve the
tenant. Identity for authenticated requests is always derived from the verified
JWT (never the request body), and is represented at runtime by ``Principal``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.core.rbac import permissions_for_role

# Password policy: 8-128 chars (hashing handles the rest).
PasswordStr = Annotated[str, StringConstraints(min_length=8, max_length=128)]
SlugStr = Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    org_slug: SlugStr = Field(..., description="Tenant subdomain / organization slug.")
    email: EmailStr
    password: PasswordStr
    full_name: str | None = Field(default=None, max_length=255)
    locale: str | None = Field(default=None, max_length=35)


class LoginRequest(BaseModel):
    org_slug: SlugStr
    email: EmailStr
    password: PasswordStr


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access-token lifetime in seconds.")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: str
    locale: str | None = None
    is_active: bool

    @field_validator("role", mode="before")
    @classmethod
    def _role_to_key(cls, value: Any) -> Any:
        # ORM ``user.role`` is a Role object; expose its ``key`` string.
        return value.key if hasattr(value, "key") else value


# --------------------------------------------------------------------------- #
# Internal principal (never serialized to clients)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Principal:
    """The authenticated caller, derived from the verified access token + DB."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    email: str
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    full_name: str | None = None
    locale: str | None = None
    is_active: bool = True
    tenant_id: str | None = None

    @classmethod
    def from_user(cls, user: Any) -> Principal:
        """Build a principal from a loaded ``User`` ORM object (role eager-loaded)."""
        role_key = user.role.key
        return cls(
            user_id=user.id,
            org_id=user.org_id,
            email=str(user.email),
            role=role_key,
            permissions=permissions_for_role(role_key),
            full_name=user.full_name,
            locale=user.locale,
            is_active=user.is_active,
            tenant_id=str(user.org_id),
        )

    def has_all_permissions(self, permissions: tuple[str, ...]) -> bool:
        return all(p in self.permissions for p in permissions)

    def has_role(self, roles: tuple[str, ...]) -> bool:
        return self.role in roles


__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "RefreshRequest",
    "LogoutRequest",
    "TokenResponse",
    "UserResponse",
    "Principal",
]
