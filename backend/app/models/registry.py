"""Data-driven registry models.

Canonical: ``category_registry`` (the extensibility seam; 8 seed rows).
New extension: ``prompt_templates`` (versioned prompt catalog for the nodes).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDpk


class CategoryRegistry(Base):
    """Category taxonomy driving routing, intake slots, SLA, and thresholds."""

    __tablename__ = "category_registry"
    __table_args__ = (
        Index("gin_category_registry_thresholds", "thresholds", postgresql_using="gin"),
    )

    category_key: Mapped[str] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(nullable=False)
    required_intake_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    retrieval_namespace: Mapped[str] = mapped_column(nullable=False, index=True)
    sla_tier: Mapped[str] = mapped_column(nullable=False)
    handoff_queue: Mapped[str] = mapped_column(nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tool_bindings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class PromptTemplate(Base, CreatedAtMixin):
    """Versioned prompt catalog per agent/node (A/B + rollback)."""

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_prompt_templates_key_version"),
        Index(
            "uq_prompt_templates_active_key",
            "key",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[UUIDpk]
    key: Mapped[str] = mapped_column(nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    content: Mapped[str] = mapped_column(nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    model_tier: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"), index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["CategoryRegistry", "PromptTemplate"]
