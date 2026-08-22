"""Canonical constants and enumerations.

Single source of truth for every enumerated value used across the platform.
Values mirror the native PostgreSQL ENUM types defined in DATABASE_DESIGN.md
(and the ``AgentState`` enums in ARCHITECTURE.md). ORM models and Pydantic
schemas import from here rather than redefining literals, so the database,
API, and orchestrator can never drift out of sync.

Python 3.12 ``StrEnum`` is used so each member compares equal to its string
value (e.g. ``TicketStatus.OPEN == "open"``), which serializes cleanly to
JSON and maps directly onto SQLAlchemy ``Enum`` columns.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# Platform / infrastructure
# ---------------------------------------------------------------------------


class Environment(StrEnum):
    """Deployment environment (drives fail-fast validation in config)."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class TokenType(StrEnum):
    """JWT token flavours issued by ``core.security``."""

    ACCESS = "access"
    REFRESH = "refresh"


class VectorStore(StrEnum):
    """Supported dense vector stores (embeddings_metadata.vector_store)."""

    CHROMADB = "chromadb"


# ---------------------------------------------------------------------------
# Identity / RBAC
# ---------------------------------------------------------------------------


class RoleKey(StrEnum):
    """Canonical RBAC roles (roles.key seed values)."""

    END_USER = "end_user"
    SUPPORT_ENGINEER = "support_engineer"
    ADMIN = "admin"
    SME_REVIEWER = "sme_reviewer"


class ActorType(StrEnum):
    """Actor that performed an audited action (audit_logs.actor_type)."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    WORKER = "worker"


# ---------------------------------------------------------------------------
# Multi-agent orchestration (AgentState)
# ---------------------------------------------------------------------------


class Decision(StrEnum):
    """Terminal routing decision emitted by the confidence gate."""

    DELIVER = "deliver"
    CLARIFY = "clarify"
    RETRY_RETRIEVAL = "retry_retrieval"
    ESCALATE = "escalate"


class SensitivityLevel(StrEnum):
    """Query sensitivity used to tighten thresholds (payment/security stricter)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentExecStatus(StrEnum):
    """Per-node execution status (agent_executions.status)."""

    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Conversation / memory
# ---------------------------------------------------------------------------


class ConversationStatus(StrEnum):
    """Chat-thread lifecycle (conversations.status)."""

    ACTIVE = "active"
    AWAITING_HUMAN = "awaiting_human"
    RESOLVED = "resolved"
    CLOSED = "closed"


class MessageRole(StrEnum):
    """Turn author (messages.role)."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# ---------------------------------------------------------------------------
# Ticketing / handoff
# ---------------------------------------------------------------------------


class TicketStatus(StrEnum):
    """Ticket lifecycle (tickets.status)."""

    OPEN = "open"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    AWAITING_USER = "awaiting_user"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


class TicketPriority(StrEnum):
    """Auto-classified ticket priority (tickets.priority)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketEventType(StrEnum):
    """Append-only ticket activity events (ticket_events.event_type)."""

    CREATED = "created"
    ASSIGNED = "assigned"
    STATUS_CHANGED = "status_changed"
    COMMENTED = "commented"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    SLA_BREACHED = "sla_breached"


class AttachmentKind(StrEnum):
    """Ticket attachment classification (ticket_attachments.kind)."""

    SCREENSHOT = "screenshot"
    LOG = "log"
    DOCUMENT = "document"
    OTHER = "other"


class AssignmentReason(StrEnum):
    """Why an assignment happened (ticket_assignments.assignment_reason)."""

    AUTO_ROUTED = "auto_routed"
    MANUAL = "manual"
    REASSIGNED = "reassigned"
    ESCALATION = "escalation"


class EscalationType(StrEnum):
    """Escalation classification (escalations.escalation_type)."""

    AI_TO_HUMAN = "ai_to_human"
    TIER1_TO_TIER2 = "tier1_to_tier2"
    SLA_BREACH = "sla_breach"
    MANUAL = "manual"


class EscalationTrigger(StrEnum):
    """What triggered an escalation (escalations.triggered_by)."""

    CONFIDENCE_GATE = "confidence_gate"
    RETRIEVAL_GATE = "retrieval_gate"
    ENGINEER = "engineer"
    SYSTEM = "system"


class NoteVisibility(StrEnum):
    """Engineer note visibility (engineer_notes.visibility)."""

    INTERNAL = "internal"
    TEAM = "team"


# ---------------------------------------------------------------------------
# Knowledge base / ingestion
# ---------------------------------------------------------------------------


class DocStatus(StrEnum):
    """Knowledge document lifecycle (kb_documents.doc_status / kb_chunks.doc_status)."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    """Origin of a knowledge document (kb_documents.source_type)."""

    ENGINEER_RESOLUTION = "engineer_resolution"
    ADMIN_UPLOAD = "admin_upload"
    MANUAL = "manual"
    IMPORTED = "imported"


class IngestionTrigger(StrEnum):
    """What kicked off an ingestion job (kb_ingestion_jobs.trigger)."""

    ENGINEER_RESOLVED = "engineer_resolved"
    ADMIN_UPLOAD = "admin_upload"
    USER_FEEDBACK = "user_feedback"


class IngestionStatus(StrEnum):
    """Ingestion pipeline stage (kb_ingestion_jobs.status)."""

    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    UPSERTING = "upserting"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalDecision(StrEnum):
    """SME/admin review outcome (kb_approvals.decision)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


# ---------------------------------------------------------------------------
# Feedback / learning
# ---------------------------------------------------------------------------


class FeedbackRating(StrEnum):
    """User feedback signal (feedback.rating)."""

    UP = "up"
    DOWN = "down"
    REOPEN = "reopen"


class LearningTrigger(StrEnum):
    """Trigger for a feedback_learner run (learning_events.trigger)."""

    ENGINEER_RESOLVED = "engineer_resolved"
    USER_FEEDBACK = "user_feedback"
    ADMIN_UPLOAD = "admin_upload"


class LearningStatus(StrEnum):
    """Feedback_learner run status (learning_events.status)."""

    DRAFTED = "drafted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    UPSERTED = "upserted"
    SIGNAL_UPDATED = "signal_updated"


# ---------------------------------------------------------------------------
# Notifications / files
# ---------------------------------------------------------------------------


class NotificationChannel(StrEnum):
    """Delivery channel (notifications.channel)."""

    IN_APP = "in_app"
    EMAIL = "email"
    QUEUE = "queue"
    WEBHOOK = "webhook"


class NotificationType(StrEnum):
    """Notification category (notifications.type)."""

    TICKET_ASSIGNED = "ticket_assigned"
    HANDOFF = "handoff"
    RESOLVED = "resolved"
    APPROVAL_REQUEST = "approval_request"
    SLA_BREACH = "sla_breach"
    MENTION = "mention"


class NotificationStatus(StrEnum):
    """Notification delivery state (notifications.status)."""

    PENDING = "pending"
    SENT = "sent"
    READ = "read"
    FAILED = "failed"


class ScanStatus(StrEnum):
    """Uploaded-file AV/PII scan gate (files.scan_status)."""

    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"


class FilePurpose(StrEnum):
    """Why a file was uploaded (files.purpose)."""

    TICKET_ATTACHMENT = "ticket_attachment"
    KB_SOURCE = "kb_source"
    AVATAR = "avatar"


# ---------------------------------------------------------------------------
# Analytics / administration
# ---------------------------------------------------------------------------


class SettingScope(StrEnum):
    """Configuration scope (system_settings.scope)."""

    GLOBAL = "global"
    ORG = "org"


class PeriodGrain(StrEnum):
    """Rollup granularity (usage_statistics.period_grain)."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ---------------------------------------------------------------------------
# Header / correlation constants
# ---------------------------------------------------------------------------

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"

__all__ = [
    "Environment",
    "TokenType",
    "VectorStore",
    "RoleKey",
    "ActorType",
    "Decision",
    "SensitivityLevel",
    "AgentExecStatus",
    "ConversationStatus",
    "MessageRole",
    "TicketStatus",
    "TicketPriority",
    "TicketEventType",
    "AttachmentKind",
    "AssignmentReason",
    "EscalationType",
    "EscalationTrigger",
    "NoteVisibility",
    "DocStatus",
    "SourceType",
    "IngestionTrigger",
    "IngestionStatus",
    "ApprovalDecision",
    "FeedbackRating",
    "LearningTrigger",
    "LearningStatus",
    "NotificationChannel",
    "NotificationType",
    "NotificationStatus",
    "ScanStatus",
    "FilePurpose",
    "SettingScope",
    "PeriodGrain",
    "REQUEST_ID_HEADER",
    "TRACE_ID_HEADER",
]
