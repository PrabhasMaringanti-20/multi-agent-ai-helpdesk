"""Identity models: users, user_sessions (canonical tables)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    CITEXT,
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDpk,
)

if TYPE_CHECKING:
    from app.models.organization import Organization, Role


class User(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """Platform user; identity is always derived from the verified JWT."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_users_org_id_email"),)

    id: Mapped[UUIDpk]
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(nullable=False)
    full_name: Mapped[str | None] = mapped_column(nullable=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    locale: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )

    organization: Mapped[Organization] = relationship(back_populates="users")
    role: Mapped[Role] = relationship(back_populates="users")
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base, CreatedAtMixin):
    """Refresh-token / session ledger supporting JWT rotation + reuse detection."""

    __tablename__ = "user_sessions"

    id: Mapped[UUIDpk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


__all__ = ["User", "UserSession"]
