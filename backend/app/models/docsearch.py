"""Document-Intelligence models: user-uploaded files + searchable passages.

Backs the "Ask your files" workspace: a user attaches PDFs / Word / text / Excel
(one chosen tab) / URLs, each is split into passages tagged with their location
(page / sheet+row / section) and the verbatim text, and searched with Postgres
full-text search. Kept separate from the helpdesk knowledge base.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Computed, ForeignKey, Index
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TenantMixin, UUIDpk

if TYPE_CHECKING:
    pass


class UploadedDocument(Base, TenantMixin, CreatedAtMixin):
    """One attached source (file or URL) owned by a user."""

    __tablename__ = "uploaded_documents"
    __table_args__ = (Index("ix_uploaded_documents_user", "org_id", "user_id"),)

    id: Mapped[UUIDpk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(nullable=False)  # pdf | docx | text | excel | url
    source_ref: Mapped[str | None] = mapped_column(nullable=True)  # url / original path
    sheet: Mapped[str | None] = mapped_column(nullable=True)  # chosen Excel tab
    chunk_count: Mapped[int] = mapped_column(nullable=False, server_default=sa_text("0"))

    chunks: Mapped[list[UploadedChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class UploadedChunk(Base, TenantMixin, CreatedAtMixin):
    """A single searchable passage with its verbatim text and location label."""

    __tablename__ = "uploaded_chunks"
    __table_args__ = (
        Index("gin_uploaded_chunks_fts", "text_fts", postgresql_using="gin"),
        Index("ix_uploaded_chunks_owner", "org_id", "user_id"),
    )

    id: Mapped[UUIDpk]
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    location: Mapped[str] = mapped_column(
        nullable=False
    )  # "Page 3", "Sheet L2 · Row 240", "Section 2"
    text: Mapped[str] = mapped_column(nullable=False)  # verbatim passage
    text_fts: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text, ''))", persisted=True),
        nullable=True,
    )

    document: Mapped[UploadedDocument] = relationship(back_populates="chunks")


__all__ = ["UploadedDocument", "UploadedChunk"]
