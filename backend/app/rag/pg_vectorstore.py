"""Postgres-backed vector store (local, no server) implementing ``VectorStore``.

Brute-force cosine over the ``rag_vectors`` table. For a single-node deployment
with a modest knowledge base (hundreds of chunks) this is exact and
sub-millisecond, needs no extra service or dependency, and persists vectors in
the same Postgres we already run. Selected via ``settings.VECTOR_STORE_BACKEND``
(``pg``); the HTTP ``ChromaVectorStore`` remains available for a Chroma server.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.rag_vector import RagVector
from app.rag.vectorstore import VectorHit


def _clean(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


class PgVectorStore:
    """Concrete Postgres brute-force vector store (implements the Protocol)."""

    def __init__(self) -> None:
        self._default_collection = get_settings().CHROMA_KB_COLLECTION

    async def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        collection: str | None = None,
    ) -> None:
        coll = collection or self._default_collection
        async with SessionFactory() as session:
            for i, vid in enumerate(ids):
                meta = dict(metadatas[i] or {})
                fields = {
                    "collection": coll,
                    "org_id": str(meta.get("org_id", "")),
                    "doc_status": str(meta.get("doc_status", "")),
                    "retrieval_namespace": _clean(meta.get("retrieval_namespace")),
                    "category_key": _clean(meta.get("category_key")),
                    "doc_id": _clean(meta.get("doc_id")),
                    "source_uri": (meta.get("source_uri") or None),
                    "version": (str(meta["version"]) if meta.get("version") is not None else None),
                    "document": documents[i],
                    "embedding": [float(x) for x in embeddings[i]],
                    "meta": meta,
                }
                row = await session.get(RagVector, str(vid))
                if row is None:
                    session.add(RagVector(id=str(vid), **fields))
                else:
                    for key, val in fields.items():
                        setattr(row, key, val)
            await session.commit()

    async def query(
        self,
        *,
        embedding: list[float],
        k: int,
        where: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> list[VectorHit]:
        coll = collection or self._default_collection
        where = where or {}
        stmt = select(RagVector).where(RagVector.collection == coll)
        for key in ("org_id", "doc_status", "retrieval_namespace", "category_key"):
            val = where.get(key)
            if val is not None:
                stmt = stmt.where(getattr(RagVector, key) == str(val))

        async with SessionFactory() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return []

        query_vec = np.asarray(embedding, dtype="float32")
        query_vec = query_vec / (float(np.linalg.norm(query_vec)) + 1e-9)
        matrix = np.asarray([r.embedding for r in rows], dtype="float32")
        norms = np.linalg.norm(matrix, axis=1) + 1e-9
        sims = (matrix @ query_vec) / norms  # cosine similarity per row

        top = np.argsort(-sims)[: max(k, 0)]
        hits: list[VectorHit] = []
        for idx in top:
            row = rows[int(idx)]
            score = (float(sims[int(idx)]) + 1.0) / 2.0  # map [-1,1] -> [0,1]
            hits.append(
                VectorHit(id=row.id, score=score, document=row.document, metadata=row.meta or {})
            )
        return hits

    async def delete(self, *, ids: list[str], collection: str | None = None) -> None:
        async with SessionFactory() as session:
            await session.execute(
                sa_delete(RagVector).where(RagVector.id.in_([str(i) for i in ids]))
            )
            await session.commit()


__all__ = ["PgVectorStore"]
