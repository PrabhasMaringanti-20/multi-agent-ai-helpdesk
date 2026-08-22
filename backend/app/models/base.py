"""SQLAlchemy declarative base, mixins, shared types, and enum bindings.

Encodes the binding conventions from DATABASE_DESIGN.md / ARCHITECTURE.md §7.1:
UUID primary keys (``gen_random_uuid()``), ``org_id`` tenancy, UTC timestamps,
soft-delete on user-content tables, native PostgreSQL ENUM types (values mirror
``core.constants``), ``jsonb`` for flexible payloads, and a deterministic naming
convention so Alembic autogenerate produces stable index/constraint names.

The native ENUM type objects are defined ONCE here and reused across model
files so a shared enum (e.g. ``doc_status`` on both ``kb_documents`` and
``kb_chunks``) maps to a single PostgreSQL type created exactly once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, MetaData, func, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.core import constants as c

# --------------------------------------------------------------------------- #
# Naming convention (stable, autogenerate-friendly)
# --------------------------------------------------------------------------- #
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# --------------------------------------------------------------------------- #
# Custom types
# --------------------------------------------------------------------------- #
class CITEXT(UserDefinedType):
    """Case-insensitive text (PostgreSQL ``citext`` extension).

    Used by ``users.email`` per the database design. The ``citext`` extension is
    created by the initial Alembic migration.
    """

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "CITEXT"


# --------------------------------------------------------------------------- #
# Declarative base
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    """Declarative base for all application (Alembic-managed) tables."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        datetime: sa.TIMESTAMP(timezone=True),
        uuid.UUID: PgUUID(as_uuid=True),
        str: sa.Text(),
        dict[str, Any]: sa.dialects.postgresql.JSONB,
        list[Any]: sa.dialects.postgresql.JSONB,
    }


# Annotated helper for the ubiquitous UUID primary key.
UUIDpk = Annotated[
    uuid.UUID,
    mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
]


# --------------------------------------------------------------------------- #
# Mixins
# --------------------------------------------------------------------------- #
class CreatedAtMixin:
    """Adds an immutable ``created_at`` (append-only / write-once tables)."""

    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class TimestampMixin:
    """Adds ``created_at`` + touch-on-update ``updated_at``."""

    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds a nullable ``deleted_at`` for policy-driven soft deletion."""

    deleted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)


class TenantMixin:
    """Adds the mandatory ``org_id`` tenancy foreign key (leads composite indexes)."""

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# --------------------------------------------------------------------------- #
# Native ENUM type bindings (defined once, reused across model files)
# --------------------------------------------------------------------------- #
def _pg_enum(enum_cls: type, name: str) -> PgEnum:
    """Build a native PostgreSQL ENUM that stores the *value* of each member."""
    return PgEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=True,
        values_callable=lambda ec: [member.value for member in ec],
        validate_strings=True,
    )


# Identity / audit
ACTOR_TYPE_ENUM = _pg_enum(c.ActorType, "actor_type")

# Orchestration
DECISION_ENUM = _pg_enum(c.Decision, "decision")
AGENT_EXEC_STATUS_ENUM = _pg_enum(c.AgentExecStatus, "agent_exec_status")

# Conversation
CONVERSATION_STATUS_ENUM = _pg_enum(c.ConversationStatus, "conversation_status")
MESSAGE_ROLE_ENUM = _pg_enum(c.MessageRole, "message_role")

# Ticketing
TICKET_STATUS_ENUM = _pg_enum(c.TicketStatus, "ticket_status")
TICKET_PRIORITY_ENUM = _pg_enum(c.TicketPriority, "ticket_priority")
TICKET_EVENT_TYPE_ENUM = _pg_enum(c.TicketEventType, "ticket_event_type")
ATTACHMENT_KIND_ENUM = _pg_enum(c.AttachmentKind, "attachment_kind")
ASSIGNMENT_REASON_ENUM = _pg_enum(c.AssignmentReason, "assignment_reason")
ESCALATION_TYPE_ENUM = _pg_enum(c.EscalationType, "escalation_type")
ESCALATION_TRIGGER_ENUM = _pg_enum(c.EscalationTrigger, "escalation_trigger")
NOTE_VISIBILITY_ENUM = _pg_enum(c.NoteVisibility, "note_visibility")

# Knowledge base
DOC_STATUS_ENUM = _pg_enum(c.DocStatus, "doc_status")
SOURCE_TYPE_ENUM = _pg_enum(c.SourceType, "source_type")
INGESTION_TRIGGER_ENUM = _pg_enum(c.IngestionTrigger, "ingestion_trigger")
INGESTION_STATUS_ENUM = _pg_enum(c.IngestionStatus, "ingestion_status")
APPROVAL_DECISION_ENUM = _pg_enum(c.ApprovalDecision, "approval_decision")
VECTOR_STORE_ENUM = _pg_enum(c.VectorStore, "vector_store")

# Feedback / learning
FEEDBACK_RATING_ENUM = _pg_enum(c.FeedbackRating, "feedback_rating")
LEARNING_TRIGGER_ENUM = _pg_enum(c.LearningTrigger, "learning_trigger")
LEARNING_STATUS_ENUM = _pg_enum(c.LearningStatus, "learning_status")

# Notifications / files
NOTIFICATION_CHANNEL_ENUM = _pg_enum(c.NotificationChannel, "notification_channel")
NOTIFICATION_TYPE_ENUM = _pg_enum(c.NotificationType, "notification_type")
NOTIFICATION_STATUS_ENUM = _pg_enum(c.NotificationStatus, "notification_status")
SCAN_STATUS_ENUM = _pg_enum(c.ScanStatus, "scan_status")
FILE_PURPOSE_ENUM = _pg_enum(c.FilePurpose, "file_purpose")

# Analytics / admin
SETTING_SCOPE_ENUM = _pg_enum(c.SettingScope, "setting_scope")
PERIOD_GRAIN_ENUM = _pg_enum(c.PeriodGrain, "period_grain")


__all__ = [
    "Base",
    "UUIDpk",
    "CITEXT",
    "CreatedAtMixin",
    "TimestampMixin",
    "SoftDeleteMixin",
    "TenantMixin",
    "NAMING_CONVENTION",
]
