"""Dense (vector) retrieval over ChromaDB via the embedding provider."""

from __future__ import annotations

from typing import Any

from app.agents.state import RetrievedChunk
from app.providers.base import EmbeddingProvider
from app.rag.vectorstore import VectorStore


class DenseRetriever:
    """Embeds the query and searches the vector store (hard tenant/status filters)."""

    def __init__(self, vectorstore: VectorStore, embedder: EmbeddingProvider) -> None:
        self._store = vectorstore
        self._embedder = embedder

    @staticmethod
    def _where(filters: dict[str, Any]) -> dict[str, Any]:
        where: dict[str, Any] = {"doc_status": "published"}
        for key in ("org_id", "retrieval_namespace", "category_key"):
            value = filters.get(key)
            if value is not None:
                where[key] = str(value)
        # ``category`` (conversations/tickets naming) maps to chunk ``category_key``.
        if "category_key" not in where and filters.get("category") is not None:
            where["category_key"] = str(filters["category"])
        return where

    async def search(self, query: str, *, filters: dict[str, Any], k: int) -> list[RetrievedChunk]:
        embedding = (await self._embedder.embed([query])).vectors[0]
        hits = await self._store.query(embedding=embedding, k=k, where=self._where(filters))
        chunks: list[RetrievedChunk] = []
        for hit in hits:
            meta = hit.metadata or {}
            chunks.append(
                RetrievedChunk(
                    chunk_id=hit.id,
                    doc_id=str(meta.get("doc_id", "")),
                    text=hit.document,
                    score=hit.score,
                    dense_score=hit.score,
                    source_uri=meta.get("source_uri"),
                    version=meta.get("version"),
                    category_key=meta.get("category_key"),
                    last_verified_at=meta.get("last_verified_at"),
                    metadata=meta,
                )
            )
        return chunks


__all__ = ["DenseRetriever"]
