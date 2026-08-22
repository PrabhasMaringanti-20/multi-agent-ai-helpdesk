"""Identity & RBAC models: organizations, roles, permissions, role_permissions.

Canonical tables: ``organizations``, ``roles``.
New extension tables (per DATABASE_DESIGN.md): ``permissions``, ``role_permissions``
(normalize the ``roles.permissions`` JWT fast-path cache).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDpk

if TYPE_CHECKING:
    from app.models.user import User


class Organization(Base, TimestampMixin):
    """Tenant root; every tenant-scoped row hangs off exactly one organization."""

    __tablename__ = "organizations"

    id: Mapped[UUIDpk]
    name: Mapped[str] = mapped_column(nullable=False)
    slug: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    users: Mapped[list[User]] = relationship(back_populates="organization")


class Role(Base, TimestampMixin):
    """Seed RBAC roles. ``permissions`` jsonb is the denormalized JWT cache."""

    __tablename__ = "roles"

    id: Mapped[UUIDpk]
    key: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(nullable=False)
    permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    users: Mapped[list[User]] = relationship(back_populates="role")
    role_permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )
    # Convenience read-only view of granted permissions via the association table.
    granted_permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", viewonly=True
    )


class Permission(Base, CreatedAtMixin):
    """Normalized permission catalog (source of truth for ``roles.permissions``)."""

    __tablename__ = "permissions"

    id: Mapped[UUIDpk]
    key: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(nullable=False)
    resource: Mapped[str] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    role_permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="permission", cascade="all, delete-orphan"
    )


class RolePermission(Base, CreatedAtMixin):
    """Association between roles and permissions (normalized RBAC grants)."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )

    id: Mapped[UUIDpk]
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    role: Mapped[Role] = relationship(back_populates="role_permissions")
    permission: Mapped[Permission] = relationship(back_populates="role_permissions")


__all__ = ["Organization", "Role", "Permission", "RolePermission"]
