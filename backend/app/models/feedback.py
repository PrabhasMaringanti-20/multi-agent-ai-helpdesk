"""Feedback & learning-signal models.

Canonical: ``feedback``, ``relevance_signals``.
New extension: ``learning_events`` (audit trail of the feedback_learner loop).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import FeedbackRating, LearningStatus, LearningTrigger
from app.models.base import (
    FEEDBACK_RATING_ENUM,
    LEARNING_STATUS_ENUM,
    LEARNING_TRIGGER_ENUM,
    Base,
    CreatedAtMixin,
    TenantMixin,
    UUIDpk,
)


class Feedback(Base, TenantMixin, CreatedAtMixin):
    """User thumbs / reopen / free-text feedback on an assistant answer."""

    __tablename__ = "feedback"
    __table_args__ = (
        Index(
            "ix_feedback_unprocessed",
            "processed_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    id: Mapped[UUIDpk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[FeedbackRating] = mapped_column(FEEDBACK_RATING_ENUM, nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(nullable=True)
    feedback_handle: Mapped[str] = mapped_column(nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )


class RelevanceSignal(Base):
    """Aggregated per-chunk/-doc signals consumed by the reranker + retrieval_gate."""

    __tablename__ = "relevance_signals"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_id", name="uq_relevance_signals_doc_chunk"),
    )

    id: Mapped[UUIDpk]
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_chunks.id", ondelete="CASCADE"), nullable=True
    )
    upvotes: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    downvotes: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    impressions: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    resolution_success: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    boost_factor: Mapped[float] = mapped_column(nullable=False, server_default=text("1.0"))
    is_quarantined: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class LearningEvent(Base, TenantMixin, CreatedAtMixin):
    """Ledger of feedback_learner runs (feedback/resolution -> KB upsert)."""

    __tablename__ = "learning_events"

    id: Mapped[UUIDpk]
    trigger: Mapped[LearningTrigger] = mapped_column(LEARNING_TRIGGER_ENUM, nullable=False)
    source_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    source_feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("feedback.id", ondelete="SET NULL"), nullable=True
    )
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="SET NULL"), nullable=True
    )
    resulting_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="SET NULL"), nullable=True
    )
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_ingestion_jobs.id", ondelete="SET NULL"), nullable=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_approvals.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[LearningStatus] = mapped_column(LEARNING_STATUS_ENUM, nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)


__all__ = ["Feedback", "RelevanceSignal", "LearningEvent"]
