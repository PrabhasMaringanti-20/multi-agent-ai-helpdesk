"""Operational models: notifications, audit_logs, files, and observability/analytics.

Canonical: ``notifications``, ``audit_logs``, ``files``, ``agent_runs``,
``analytics_events``.
New extension: ``agent_executions``, ``confidence_scores``, ``usage_statistics``,
``system_settings``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    ActorType,
    AgentExecStatus,
    Decision,
    FilePurpose,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    PeriodGrain,
    ScanStatus,
    SettingScope,
)
from app.models.base import (
    ACTOR_TYPE_ENUM,
    AGENT_EXEC_STATUS_ENUM,
    DECISION_ENUM,
    FILE_PURPOSE_ENUM,
    NOTIFICATION_CHANNEL_ENUM,
    NOTIFICATION_STATUS_ENUM,
    NOTIFICATION_TYPE_ENUM,
    PERIOD_GRAIN_ENUM,
    SCAN_STATUS_ENUM,
    SETTING_SCOPE_ENUM,
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDpk,
)


class Notification(Base, TenantMixin, CreatedAtMixin):
    """In-app + outbound notification ledger (sends are gated)."""

    __tablename__ = "notifications"

    id: Mapped[UUIDpk]
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel: Mapped[NotificationChannel] = mapped_column(NOTIFICATION_CHANNEL_ENUM, nullable=False)
    type: Mapped[NotificationType] = mapped_column(
        NOTIFICATION_TYPE_ENUM, nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[NotificationStatus] = mapped_column(
        NOTIFICATION_STATUS_ENUM,
        nullable=False,
        server_default=NotificationStatus.PENDING.value,
        index=True,
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)


class AuditLog(Base, TenantMixin):
    """Append-only, immutable audit log (INSERT/SELECT only by DB grant)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("brin_audit_logs_created_at", "created_at", postgresql_using="brin"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_trace_id", "trace_id"),
    )

    id: Mapped[UUIDpk]
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(ACTOR_TYPE_ENUM, nullable=False)
    action: Mapped[str] = mapped_column(nullable=False)
    resource_type: Mapped[str] = mapped_column(nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    trace_id: Mapped[str | None] = mapped_column(nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    # Write-once: no updated_at / deleted_at by policy.
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class File(Base, TenantMixin, SoftDeleteMixin):
    """Uploaded blobs (ticket attachments + KB source docs)."""

    __tablename__ = "files"
    __table_args__ = (Index("ix_files_checksum", "checksum"),)

    id: Mapped[UUIDpk]
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(nullable=False)
    content_type: Mapped[str] = mapped_column(nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_uri: Mapped[str] = mapped_column(nullable=False)
    checksum: Mapped[str] = mapped_column(nullable=False)
    scan_status: Mapped[ScanStatus] = mapped_column(
        SCAN_STATUS_ENUM,
        nullable=False,
        server_default=ScanStatus.PENDING.value,
        index=True,
    )
    purpose: Mapped[FilePurpose] = mapped_column(FILE_PURPOSE_ENUM, nullable=False, index=True)
    # created_at only (no updated_at); deleted_at from SoftDeleteMixin.
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRun(Base, CreatedAtMixin):
    """One row per graph execution turn (observability spine)."""

    __tablename__ = "agent_runs"

    id: Mapped[UUIDpk]
    trace_id: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_id: Mapped[int] = mapped_column(nullable=False)
    decision: Mapped[Decision] = mapped_column(DECISION_ENUM, nullable=False, index=True)
    node_path: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    retry_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    clarification_rounds: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    final_confidence: Mapped[float | None] = mapped_column(nullable=True)
    grounding_score: Mapped[float | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    token_cost: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(nullable=True)

    executions: Mapped[list[AgentExecution]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    confidence_scores: Mapped[list[ConfidenceScore]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentExecution(Base, CreatedAtMixin):
    """Per-node execution within an agent_runs turn (expands node_path)."""

    __tablename__ = "agent_executions"
    __table_args__ = (Index("ix_agent_executions_run_sequence", "run_id", "sequence_no"),)

    id: Mapped[UUIDpk]
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(nullable=False)
    sequence_no: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[AgentExecStatus] = mapped_column(AGENT_EXEC_STATUS_ENUM, nullable=False)
    model_tier: Mapped[str | None] = mapped_column(nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    input_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="executions")


class ConfidenceScore(Base, CreatedAtMixin):
    """Per-decision confidence component breakdown (headline scores on agent_runs)."""

    __tablename__ = "confidence_scores"

    id: Mapped[UUIDpk]
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    intent_confidence: Mapped[float] = mapped_column(nullable=False)
    retrieval_score: Mapped[float] = mapped_column(nullable=False)
    grounding_score: Mapped[float] = mapped_column(nullable=False)
    final_confidence: Mapped[float] = mapped_column(nullable=False)
    contradiction_flag: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    decision: Mapped[Decision] = mapped_column(DECISION_ENUM, nullable=False)
    threshold_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    run: Mapped[AgentRun] = relationship(back_populates="confidence_scores")


class AnalyticsEvent(Base, TenantMixin):
    """Denormalized event stream feeding the Analytics dashboard."""

    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("brin_analytics_events_occurred_at", "occurred_at", postgresql_using="brin"),
        Index("ix_analytics_events_event_type", "event_type"),
        Index("ix_analytics_events_category", "category"),
    )

    id: Mapped[UUIDpk]
    event_type: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str | None] = mapped_column(nullable=True)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class UsageStatistic(Base, TenantMixin, CreatedAtMixin):
    """Precomputed rollups over analytics_events for dashboards."""

    __tablename__ = "usage_statistics"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "period_grain",
            "period_start",
            "metric_key",
            "category",
            name="uq_usage_statistics_rollup",
        ),
    )

    id: Mapped[UUIDpk]
    period_start: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    period_grain: Mapped[PeriodGrain] = mapped_column(PERIOD_GRAIN_ENUM, nullable=False)
    metric_key: Mapped[str] = mapped_column(nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(nullable=True)
    dimensions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    value: Mapped[float] = mapped_column(sa.Numeric, nullable=False)
    count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))


class SystemSetting(Base, TimestampMixin):
    """Typed global / per-org configuration (org_id NULL == global)."""

    __tablename__ = "system_settings"
    __table_args__ = (
        UniqueConstraint("scope", "org_id", "key", name="uq_system_settings_scope_key"),
    )

    id: Mapped[UUIDpk]
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[SettingScope] = mapped_column(SETTING_SCOPE_ENUM, nullable=False)
    key: Mapped[str] = mapped_column(nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    value_type: Mapped[str] = mapped_column(nullable=False)
    is_secret: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    description: Mapped[str | None] = mapped_column(nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = [
    "Notification",
    "AuditLog",
    "File",
    "AgentRun",
    "AgentExecution",
    "ConfidenceScore",
    "AnalyticsEvent",
    "UsageStatistic",
    "SystemSetting",
]
