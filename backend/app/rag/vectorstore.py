"""ChromaDB vector store wrapper + ``VectorStore`` Protocol.

The Protocol lets the dense retriever depend on an abstraction so tests inject a
fake store. ``ChromaVectorStore`` lazily imports ``chromadb`` and runs its sync
client off the event loop; it targets the canonical ``kb_chunks`` /
``kb_chunks_pending`` collections (§7.10) with the vector id == ``kb_chunks.id``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.config import get_settings
from app.core.exceptions import RetrievalError
from app.core.logging import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    async def query(
        self,
        *,
        embedding: list[float],
        k: int,
        where: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> list[VectorHit]: ...

    async def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        collection: str | None = None,
    ) -> None: ...

    async def delete(self, *, ids: list[str], collection: str | None = None) -> None: ...


class ChromaVectorStore:
    """Concrete ChromaDB-backed vector store (SDK imported lazily)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.CHROMA_HOST
        self._port = settings.CHROMA_PORT
        self._default_collection = settings.CHROMA_KB_COLLECTION
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import chromadb
            except ImportError as exc:  # pragma: no cover
                raise RetrievalError("chromadb is not installed.") from exc
            self._client = chromadb.HttpClient(host=self._host, port=self._port)
        return self._client

    def _collection(self, name: str | None) -> Any:
        client = self._get_client()
        return client.get_or_create_collection(name or self._default_collection)

    async def query(
        self,
        *,
        embedding: list[float],
        k: int,
        where: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> list[VectorHit]:
        def _run() -> list[VectorHit]:
            coll = self._collection(collection)
            res = coll.query(
                query_embeddings=[embedding],
                n_results=k,
                where=where or None,
                include=["documents", "metadatas", "distances"],
            )
            ids = (res.get("ids") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            hits: list[VectorHit] = []
            for i, vid in enumerate(ids):
                distance = dists[i] if i < len(dists) else 1.0
                hits.append(
                    VectorHit(
                        id=vid,
                        score=1.0 / (1.0 + float(distance)),  # distance -> similarity
                        document=docs[i] if i < len(docs) else "",
                        metadata=metas[i] if i < len(metas) else {},
                    )
                )
            return hits

        try:
            return await asyncio.to_thread(_run)
        except RetrievalError:
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Chroma query failed: %s", exc)
            raise RetrievalError(f"Vector query failed: {exc}") from exc

    async def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        collection: str | None = None,
    ) -> None:
        def _run() -> None:
            coll = self._collection(collection)
            coll.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

        await asyncio.to_thread(_run)

    async def delete(self, *, ids: list[str], collection: str | None = None) -> None:
        def _run() -> None:
            self._collection(collection).delete(ids=ids)

        await asyncio.to_thread(_run)


def get_vector_store() -> VectorStore:
    """Return the configured vector store.

    ``pg`` (default) uses the local Postgres-backed brute-force store — no
    separate Chroma server, no extra downloads. Any other value falls back to
    the HTTP ``ChromaVectorStore`` (for when a Chroma server is available).
    """
    backend = (getattr(get_settings(), "VECTOR_STORE_BACKEND", "pg") or "pg").lower()
    if backend == "pg":
        from app.rag.pg_vectorstore import PgVectorStore

        return PgVectorStore()
    return ChromaVectorStore()


async def check_chroma() -> bool:
    """Best-effort readiness heartbeat; never raises."""
    try:
        store = ChromaVectorStore()
        client = store._get_client()
        await asyncio.to_thread(client.heartbeat)
        return True
    except Exception as exc:  # noqa: BLE001 - readiness must not raise
        _logger.warning("Chroma readiness check failed: %s", exc)
        return False


async def check_vector_store() -> bool:
    """Readiness check for whichever backend is actually configured.

    ``check_chroma`` used to run unconditionally here even when
    ``VECTOR_STORE_BACKEND=pg`` (the default - see ``get_vector_store``), so
    ``/health/ready`` would hang trying to reach a Chroma server that was never
    part of the deployment. This checks the store that's actually in use.
    """
    backend = (getattr(get_settings(), "VECTOR_STORE_BACKEND", "pg") or "pg").lower()
    if backend != "pg":
        return await check_chroma()
    try:
        from sqlalchemy import select

        from app.db.session import SessionFactory
        from app.models.rag_vector import RagVector

        async with SessionFactory() as session:
            await session.execute(select(RagVector.id).limit(1))
        return True
    except Exception as exc:  # noqa: BLE001 - readiness must not raise
        _logger.warning("Postgres vector store readiness check failed: %s", exc)
        return False


__all__ = [
    "VectorHit",
    "VectorStore",
    "ChromaVectorStore",
    "get_vector_store",
    "check_chroma",
    "check_vector_store",
]
