"""Knowledge-base & ingestion models.

Canonical: ``kb_documents``, ``kb_chunks``, ``kb_ingestion_jobs``, ``kb_approvals``.
New extension: ``kb_document_versions``, ``embeddings_metadata``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa

# NOTE: sqlalchemy.text is aliased to ``sa_text`` because KbChunk defines a
# column literally named ``text`` (per the DB design), which would otherwise
# shadow the imported name inside the class body.
from sqlalchemy import Computed, ForeignKey, Index, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    ApprovalDecision,
    DocStatus,
    IngestionStatus,
    IngestionTrigger,
    SourceType,
    VectorStore,
)
from app.models.base import (
    APPROVAL_DECISION_ENUM,
    DOC_STATUS_ENUM,
    INGESTION_STATUS_ENUM,
    INGESTION_TRIGGER_ENUM,
    SOURCE_TYPE_ENUM,
    VECTOR_STORE_ENUM,
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDpk,
)


class KbDocument(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """Logical source document; versioned; id == AgentState.kb_doc_id."""

    __tablename__ = "kb_documents"
    __table_args__ = (Index("ix_kb_documents_checksum", "checksum"),)

    id: Mapped[UUIDpk]
    title: Mapped[str] = mapped_column(nullable=False, index=True)
    source_type: Mapped[SourceType] = mapped_column(SOURCE_TYPE_ENUM, nullable=False, index=True)
    origin_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(
        ForeignKey("category_registry.category_key", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    retrieval_namespace: Mapped[str] = mapped_column(nullable=False, index=True)
    doc_status: Mapped[DocStatus] = mapped_column(
        DOC_STATUS_ENUM,
        nullable=False,
        server_default=DocStatus.DRAFT.value,
        index=True,
    )
    version: Mapped[int] = mapped_column(nullable=False, server_default=sa_text("1"))
    source_uri: Mapped[str | None] = mapped_column(nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True, index=True
    )
    checksum: Mapped[str] = mapped_column(nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    chunks: Mapped[list[KbChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    versions: Mapped[list[KbDocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[KbApproval]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list[KbIngestionJob]] = relationship(back_populates="document")


class KbChunk(Base, TenantMixin, TimestampMixin):
    """Chunk metadata + provenance mirroring ChromaDB vectors; holds the FTS column."""

    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index(
            "ix_kb_chunks_retrieval_filter",
            "org_id",
            "retrieval_namespace",
            "doc_status",
            "last_verified_at",
        ),
        Index("gin_kb_chunks_text_fts", "text_fts", postgresql_using="gin"),
    )

    id: Mapped[UUIDpk]
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NOTE: this FK is intentionally named ``category_key`` (kb_chunks) while
    # conversations/tickets/kb_documents name the same FK ``category`` -- an
    # intentional divergence carried over from the approved database design.
    category_key: Mapped[str] = mapped_column(
        ForeignKey("category_registry.category_key", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    retrieval_namespace: Mapped[str] = mapped_column(nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(nullable=False)
    text_fts: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text, ''))", persisted=True),
        nullable=True,
    )
    token_count: Mapped[int] = mapped_column(nullable=False, server_default=sa_text("0"))
    version: Mapped[int] = mapped_column(nullable=False, server_default=sa_text("1"))
    doc_status: Mapped[DocStatus] = mapped_column(DOC_STATUS_ENUM, nullable=False, index=True)
    source_uri: Mapped[str | None] = mapped_column(nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True, index=True
    )
    embedding_model_id: Mapped[str] = mapped_column(nullable=False)

    document: Mapped[KbDocument] = relationship(back_populates="chunks")
    embedding_metadata: Mapped[EmbeddingsMetadata | None] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", uselist=False
    )


class KbDocumentVersion(Base, CreatedAtMixin):
    """Immutable version history / snapshot of a kb_document."""

    __tablename__ = "kb_document_versions"
    __table_args__ = (
        UniqueConstraint("doc_id", "version", name="uq_kb_document_versions_doc_version"),
    )

    id: Mapped[UUIDpk]
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(nullable=False)
    source_uri: Mapped[str | None] = mapped_column(nullable=True)
    doc_status: Mapped[DocStatus] = mapped_column(DOC_STATUS_ENUM, nullable=False)
    checksum: Mapped[str] = mapped_column(nullable=False)
    change_summary: Mapped[str | None] = mapped_column(nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    document: Mapped[KbDocument] = relationship(back_populates="versions")


class EmbeddingsMetadata(Base, CreatedAtMixin):
    """Per-chunk embedding provenance; drives re-embed / drift detection."""

    __tablename__ = "embeddings_metadata"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "embedding_model_id", name="uq_embeddings_metadata_chunk_model"
        ),
    )

    id: Mapped[UUIDpk]
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_chunks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding_model_id: Mapped[str] = mapped_column(nullable=False, index=True)
    model_dim: Mapped[int] = mapped_column(nullable=False)
    vector_store: Mapped[VectorStore] = mapped_column(
        VECTOR_STORE_ENUM,
        nullable=False,
        server_default=VectorStore.CHROMADB.value,
    )
    collection_name: Mapped[str] = mapped_column(nullable=False)
    vector_id: Mapped[str] = mapped_column(nullable=False)
    vector_checksum: Mapped[str | None] = mapped_column(nullable=True)
    is_stale: Mapped[bool] = mapped_column(
        nullable=False, server_default=sa_text("false"), index=True
    )
    embedded_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
    )

    chunk: Mapped[KbChunk] = relationship(back_populates="embedding_metadata")


class KbIngestionJob(Base, TenantMixin, CreatedAtMixin):
    """Tracks the async parse -> chunk -> embed -> upsert pipeline."""

    __tablename__ = "kb_ingestion_jobs"

    id: Mapped[UUIDpk]
    doc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[IngestionTrigger] = mapped_column(
        INGESTION_TRIGGER_ENUM, nullable=False, index=True
    )
    status: Mapped[IngestionStatus] = mapped_column(
        INGESTION_STATUS_ENUM,
        nullable=False,
        server_default=IngestionStatus.QUEUED.value,
        index=True,
    )
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)

    document: Mapped[KbDocument | None] = relationship(back_populates="ingestion_jobs")


class KbApproval(Base, CreatedAtMixin):
    """SME/admin review gate flipping pending_review -> published."""

    __tablename__ = "kb_approvals"

    id: Mapped[UUIDpk]
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        APPROVAL_DECISION_ENUM,
        nullable=False,
        server_default=ApprovalDecision.PENDING.value,
        index=True,
    )
    review_notes: Mapped[str | None] = mapped_column(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)

    document: Mapped[KbDocument] = relationship(back_populates="approvals")


__all__ = [
    "KbDocument",
    "KbChunk",
    "KbDocumentVersion",
    "EmbeddingsMetadata",
    "KbIngestionJob",
    "KbApproval",
]
