"""Conversation & memory models.

Canonical tables: ``conversations``, ``messages``, ``conversation_summaries``,
``memory_facts``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ConversationStatus, Decision, MessageRole
from app.models.base import (
    CONVERSATION_STATUS_ENUM,
    DECISION_ENUM,
    MESSAGE_ROLE_ENUM,
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDpk,
)


class Conversation(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """One chat thread; ``id`` doubles as the LangGraph ``thread_id``."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_last_message", "user_id", "last_message_at"),)

    id: Mapped[UUIDpk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        CONVERSATION_STATUS_ENUM,
        nullable=False,
        server_default=ConversationStatus.ACTIVE.value,
        index=True,
    )
    category: Mapped[str | None] = mapped_column(
        ForeignKey("category_registry.category_key", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True, index=True
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    summaries: Mapped[list[ConversationSummary]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, CreatedAtMixin):
    """Append-only turn log; source for ``add_messages`` hydration."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_turn", "conversation_id", "turn_id"),
        Index("ix_messages_trace_id", "trace_id"),
    )

    id: Mapped[UUIDpk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_id: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[MessageRole] = mapped_column(MESSAGE_ROLE_ENUM, nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    decision: Mapped[Decision | None] = mapped_column(DECISION_ENUM, nullable=True)
    trace_id: Mapped[str] = mapped_column(nullable=False)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ConversationSummary(Base, CreatedAtMixin):
    """Rolling long-term summary; one current row per conversation."""

    __tablename__ = "conversation_summaries"
    __table_args__ = (
        Index(
            "uq_conversation_summaries_current",
            "conversation_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[UUIDpk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary_text: Mapped[str] = mapped_column(nullable=False)
    covered_through_turn: Mapped[int] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"), index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="summaries")


class MemoryFact(Base, TenantMixin, TimestampMixin):
    """Durable per-user facts surviving across conversations."""

    __tablename__ = "memory_facts"
    __table_args__ = (UniqueConstraint("user_id", "fact_key", name="uq_memory_facts_user_key"),)

    id: Mapped[UUIDpk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_key: Mapped[str] = mapped_column(nullable=False, index=True)
    fact_value: Mapped[str] = mapped_column(nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, server_default=text("1.0"))
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)


__all__ = ["Conversation", "Message", "ConversationSummary", "MemoryFact"]
