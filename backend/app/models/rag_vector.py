"""Local vector index table backing :class:`PgVectorStore` (dense KB retrieval).

One row per embedded chunk: the vector plus the metadata the dense retriever
filters on. Kept deliberately generic (a plain table, not wired into the tenant
FK graph) so it mirrors the ``VectorStore`` Protocol rather than the KB schema.
Populated by ``scripts/reindex_kb.py`` and the KB ingestion worker. Chosen over a
separate Chroma server because a single-node deployment with a modest KB does
exact brute-force cosine in-process with zero extra services or downloads.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RagVector(Base, TimestampMixin):
    __tablename__ = "rag_vectors"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # == kb_chunks.id
    collection: Mapped[str] = mapped_column(String, nullable=False, index=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    doc_status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    retrieval_namespace: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    category_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    doc_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    document: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_rag_vectors_coll_org_status", "collection", "org_id", "doc_status"),
    )


__all__ = ["RagVector"]
