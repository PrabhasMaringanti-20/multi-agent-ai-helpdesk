"""Data access for the knowledge base: documents, chunks, versions, approvals."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.core.constants import DocStatus
from app.models.knowledge import (
    KbApproval,
    KbChunk,
    KbDocument,
    KbDocumentVersion,
)
from app.repositories.base import BaseRepository


def _or_tsquery_terms(query: str) -> str | None:
    """Turn free text into an OR-joined ``to_tsquery`` string.

    ``plainto_tsquery`` ANDs every term, so a natural-language question
    ("VPN error 800 on Windows, how do I fix it?") only matches a chunk that
    contains *all* of those words — usually nothing. ORing the terms lets any
    overlap surface a candidate, and ``ts_rank`` still orders by match quality.
    Input is reduced to alphanumeric tokens, so the result is always valid
    ``to_tsquery`` syntax (no injection surface).
    """
    seen: list[str] = []
    for token in re.findall(r"\w+", query.lower()):
        if len(token) > 1 and token not in seen:
            seen.append(token)
    return " | ".join(seen) if seen else None


class KnowledgeRepository(BaseRepository[KbDocument]):
    model = KbDocument

    async def get_document(self, document_id: uuid.UUID, org_id: uuid.UUID) -> KbDocument | None:
        stmt = (
            select(KbDocument)
            .where(
                KbDocument.id == document_id,
                KbDocument.org_id == org_id,
                KbDocument.deleted_at.is_(None),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_published(
        self,
        org_id: uuid.UUID,
        *,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[KbDocument]:
        stmt = select(KbDocument).where(
            KbDocument.org_id == org_id,
            KbDocument.doc_status == DocStatus.PUBLISHED,
            KbDocument.deleted_at.is_(None),
        )
        if category is not None:
            stmt = stmt.where(KbDocument.category == category)
        stmt = stmt.order_by(KbDocument.last_verified_at.desc().nullslast())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search_documents(
        self,
        org_id: uuid.UUID,
        *,
        q: str | None = None,
        category: str | None = None,
        statuses: Sequence[DocStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[KbDocument]:
        """Role-aware KB listing with optional title search + category/status filters."""
        stmt = select(KbDocument).where(
            KbDocument.org_id == org_id, KbDocument.deleted_at.is_(None)
        )
        if statuses:
            stmt = stmt.where(KbDocument.doc_status.in_(list(statuses)))
        if category:
            stmt = stmt.where(KbDocument.category == category)
        if q:
            stmt = stmt.where(KbDocument.title.ilike(f"%{q}%"))
        stmt = stmt.order_by(KbDocument.updated_at.desc().nullslast()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_documents(
        self,
        org_id: uuid.UUID,
        *,
        q: str | None = None,
        category: str | None = None,
        statuses: Sequence[DocStatus] | None = None,
    ) -> int:
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(KbDocument)
            .where(KbDocument.org_id == org_id, KbDocument.deleted_at.is_(None))
        )
        if statuses:
            stmt = stmt.where(KbDocument.doc_status.in_(list(statuses)))
        if category:
            stmt = stmt.where(KbDocument.category == category)
        if q:
            stmt = stmt.where(KbDocument.title.ilike(f"%{q}%"))
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_by_checksum(self, org_id: uuid.UUID, checksum: str) -> KbDocument | None:
        stmt = (
            select(KbDocument)
            .where(KbDocument.org_id == org_id, KbDocument.checksum == checksum)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_chunks(self, doc_id: uuid.UUID) -> Sequence[KbChunk]:
        stmt = select(KbChunk).where(KbChunk.doc_id == doc_id).order_by(KbChunk.chunk_index.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def add_chunk(
        self,
        *,
        chunk_id: uuid.UUID | str,
        doc_id: uuid.UUID,
        org_id: uuid.UUID,
        category_key: str,
        retrieval_namespace: str,
        chunk_index: int,
        text: str,
        embedding_model_id: str,
        doc_status: DocStatus = DocStatus.PENDING_REVIEW,
        version: int = 1,
        token_count: int = 0,
        source_uri: str | None = None,
    ) -> KbChunk:
        """Persist a single chunk row (``text_fts`` is a generated column)."""
        chunk = KbChunk(
            id=chunk_id if isinstance(chunk_id, uuid.UUID) else uuid.UUID(str(chunk_id)),
            doc_id=doc_id,
            org_id=org_id,
            category_key=category_key,
            retrieval_namespace=retrieval_namespace,
            chunk_index=chunk_index,
            text=text,
            embedding_model_id=embedding_model_id,
            doc_status=doc_status,
            version=version,
            token_count=token_count,
            source_uri=source_uri,
        )
        self.session.add(chunk)
        await self.session.flush()
        return chunk

    async def add_version(
        self,
        *,
        doc_id: uuid.UUID,
        version: int,
        title: str,
        doc_status: DocStatus,
        checksum: str,
        source_uri: str | None = None,
        change_summary: str | None = None,
        snapshot: dict | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> KbDocumentVersion:
        row = KbDocumentVersion(
            doc_id=doc_id,
            version=version,
            title=title,
            doc_status=doc_status,
            checksum=checksum,
            source_uri=source_uri,
            change_summary=change_summary,
            snapshot=snapshot or {},
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_versions(self, doc_id: uuid.UUID) -> Sequence[KbDocumentVersion]:
        stmt = (
            select(KbDocumentVersion)
            .where(KbDocumentVersion.doc_id == doc_id)
            .order_by(KbDocumentVersion.version.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search_fts(
        self,
        org_id: uuid.UUID,
        query: str,
        *,
        namespace: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[tuple[KbChunk, float]]:
        """Sparse/BM25 retrieval over published chunks via the tsvector index.

        Returns ``(chunk, ts_rank)`` pairs ordered by relevance. This is the
        sparse half of hybrid retrieval; it reuses the ``kb_chunks.text_fts``
        GIN index defined on the model.
        """
        or_terms = _or_tsquery_terms(query)
        if not or_terms:
            return []
        tsquery = func.to_tsquery("english", or_terms)
        rank = func.ts_rank(KbChunk.text_fts, tsquery)
        stmt = select(KbChunk, rank.label("rank")).where(
            KbChunk.org_id == org_id,
            KbChunk.doc_status == DocStatus.PUBLISHED,
            KbChunk.text_fts.op("@@")(tsquery),
        )
        if namespace is not None:
            stmt = stmt.where(KbChunk.retrieval_namespace == namespace)
        if category is not None:
            stmt = stmt.where(KbChunk.category_key == category)
        stmt = stmt.order_by(rank.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return [(row[0], float(row[1] or 0.0)) for row in result.all()]

    async def list_pending_approvals(self, doc_id: uuid.UUID) -> Sequence[KbApproval]:
        stmt = (
            select(KbApproval)
            .where(KbApproval.doc_id == doc_id)
            .order_by(KbApproval.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


__all__ = ["KnowledgeRepository"]
