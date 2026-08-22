"""Sparse (BM25/FTS) retrieval over PostgreSQL ``kb_chunks.text_fts``."""

from __future__ import annotations

import uuid
from typing import Any

from app.agents.state import RetrievedChunk
from app.repositories.kb_repo import KnowledgeRepository


class SparseRetriever:
    """Postgres full-text retrieval; the sparse half of hybrid retrieval."""

    def __init__(self, kb_repo: KnowledgeRepository) -> None:
        self._kb = kb_repo

    async def search(self, query: str, *, filters: dict[str, Any], k: int) -> list[RetrievedChunk]:
        org_id = filters.get("org_id")
        if org_id is None:
            return []
        rows = await self._kb.search_fts(
            org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id)),
            query,
            namespace=filters.get("retrieval_namespace"),
            category=filters.get("category_key") or filters.get("category"),
            limit=k,
        )
        return [
            RetrievedChunk(
                chunk_id=str(chunk.id),
                doc_id=str(chunk.doc_id),
                text=chunk.text,
                score=rank,
                sparse_score=rank,
                source_uri=chunk.source_uri,
                version=chunk.version,
                category_key=chunk.category_key,
                last_verified_at=(
                    chunk.last_verified_at.isoformat() if chunk.last_verified_at else None
                ),
            )
            for chunk, rank in rows
        ]


__all__ = ["SparseRetriever"]
