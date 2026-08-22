"""Ticketing & handoff models.

Canonical: ``tickets``, ``ticket_events``, ``ticket_attachments``.
New extension: ``engineer_notes``, ``ticket_assignments``, ``escalations``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    AssignmentReason,
    AttachmentKind,
    EscalationTrigger,
    EscalationType,
    NoteVisibility,
    TicketEventType,
    TicketPriority,
    TicketStatus,
)
from app.models.base import (
    ASSIGNMENT_REASON_ENUM,
    ATTACHMENT_KIND_ENUM,
    ESCALATION_TRIGGER_ENUM,
    ESCALATION_TYPE_ENUM,
    NOTE_VISIBILITY_ENUM,
    TICKET_EVENT_TYPE_ENUM,
    TICKET_PRIORITY_ENUM,
    TICKET_STATUS_ENUM,
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDpk,
)


class Ticket(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """Structured, engineer-ready support ticket; ``id`` == AgentState.ticket_id."""

    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_queue_status_priority", "assigned_queue", "status", "priority"),
        Index("ix_tickets_engineer_status", "assigned_engineer_id", "status"),
        Index(
            "ix_tickets_sla_due_open",
            "sla_due_at",
            postgresql_where=text("status NOT IN ('resolved', 'closed')"),
        ),
        Index("gin_tickets_intake_fields", "intake_fields", postgresql_using="gin"),
    )

    id: Mapped[UUIDpk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_engineer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(
        ForeignKey("category_registry.category_key", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        TICKET_PRIORITY_ENUM, nullable=False, index=True
    )
    status: Mapped[TicketStatus] = mapped_column(
        TICKET_STATUS_ENUM,
        nullable=False,
        server_default=TicketStatus.OPEN.value,
        index=True,
    )
    assigned_queue: Mapped[str] = mapped_column(nullable=False, index=True)
    subject: Mapped[str] = mapped_column(nullable=False)
    intake_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    escalation_reason: Mapped[str] = mapped_column(nullable=False)
    final_confidence: Mapped[float | None] = mapped_column(nullable=True)
    engineer_hints: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    redacted_transcript: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sla_due_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))

    events: Mapped[list[TicketEvent]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    attachments: Mapped[list[TicketAttachment]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    notes: Mapped[list[EngineerNote]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[TicketAssignment]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    escalations: Mapped[list[Escalation]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )


class TicketEvent(Base, CreatedAtMixin):
    """Append-only ticket state-transition + activity log."""

    __tablename__ = "ticket_events"

    id: Mapped[UUIDpk]
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[TicketEventType] = mapped_column(
        TICKET_EVENT_TYPE_ENUM, nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(nullable=True)
    to_status: Mapped[str | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="events")


class TicketAttachment(Base, CreatedAtMixin):
    """Join between tickets and uploaded files."""

    __tablename__ = "ticket_attachments"

    id: Mapped[UUIDpk]
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[AttachmentKind] = mapped_column(ATTACHMENT_KIND_ENUM, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="attachments")


class EngineerNote(Base, TimestampMixin, SoftDeleteMixin):
    """Internal (non-user-facing) engineer notes on a ticket."""

    __tablename__ = "engineer_notes"

    id: Mapped[UUIDpk]
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(nullable=False)
    visibility: Mapped[NoteVisibility] = mapped_column(
        NOTE_VISIBILITY_ENUM,
        nullable=False,
        server_default=NoteVisibility.INTERNAL.value,
    )
    is_pinned: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    ticket: Mapped[Ticket] = relationship(back_populates="notes")


class TicketAssignment(Base, CreatedAtMixin):
    """Assignment history ledger (tickets.assigned_engineer_id is the current pointer)."""

    __tablename__ = "ticket_assignments"
    __table_args__ = (
        Index(
            "uq_ticket_assignments_current",
            "ticket_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[UUIDpk]
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_to_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_queue: Mapped[str] = mapped_column(nullable=False, index=True)
    assignment_reason: Mapped[AssignmentReason] = mapped_column(
        ASSIGNMENT_REASON_ENUM, nullable=False
    )
    is_current: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"), index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )

    ticket: Mapped[Ticket] = relationship(back_populates="assignments")


class Escalation(Base, CreatedAtMixin):
    """Escalation ledger (inline data also on tickets/ticket_events)."""

    __tablename__ = "escalations"

    id: Mapped[UUIDpk]
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_level: Mapped[str | None] = mapped_column(nullable=True)
    to_level: Mapped[str | None] = mapped_column(nullable=True)
    escalation_type: Mapped[EscalationType] = mapped_column(ESCALATION_TYPE_ENUM, nullable=False)
    reason_code: Mapped[str] = mapped_column(nullable=False, index=True)
    final_confidence: Mapped[float | None] = mapped_column(nullable=True)
    triggered_by: Mapped[EscalationTrigger] = mapped_column(ESCALATION_TRIGGER_ENUM, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="escalations")


__all__ = [
    "Ticket",
    "TicketEvent",
    "TicketAttachment",
    "EngineerNote",
    "TicketAssignment",
    "Escalation",
]
