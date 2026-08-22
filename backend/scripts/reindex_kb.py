"""Re-embed all published KB chunks into the configured vector store.

Reads existing ``kb_chunks`` rows (the sparse/BM25 side already seeded) and
populates the dense index so hybrid retrieval has real semantic vectors. The
vector id is kept equal to ``kb_chunks.id`` so dense and sparse hits line up.
Idempotent: re-running upserts (overwrites) the same ids.

Run from backend/ with the app importable:
    PYTHONPATH=backend python scripts/reindex_kb.py
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.db.base import Base
from app.db.session import SessionFactory, engine
from app.models.knowledge import KbChunk
from app.models.rag_vector import RagVector
from app.providers.registry import get_embedding_provider
from app.rag.vectorstore import get_vector_store
from sqlalchemy import select

BATCH = 10  # chunks per embed() call — keeps us well under embedding rate limits


def _status(value: object) -> str:
    return getattr(value, "value", None) or str(value)


async def main() -> None:
    settings = get_settings()
    collection = settings.CHROMA_KB_COLLECTION

    # Ensure the rag_vectors table exists (create_all is idempotent).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as session:
        rows = list(
            (await session.execute(select(KbChunk).where(KbChunk.doc_status == "published")))
            .scalars()
            .all()
        )
        # Resumable: skip chunks already embedded (so a re-run after a rate-limit
        # only does the remainder).
        indexed = set(
            (await session.execute(select(RagVector.id).where(RagVector.collection == collection)))
            .scalars()
            .all()
        )

    if indexed:
        before = len(rows)
        rows = [c for c in rows if str(c.id) not in indexed]
        print(f"{len(indexed)} chunk(s) already indexed; {len(rows)} of {before} remaining.")
    if not rows:
        print(f"Vector index '{collection}' already complete — nothing to do.")
        return

    embedder = get_embedding_provider()
    store = get_vector_store()
    print(
        f"Re-embedding {len(rows)} published chunks with {embedder.model_id} "
        f"(dim={embedder.dim}) into '{collection}' via {type(store).__name__} ..."
    )

    async def _embed_batch(texts: list[str]) -> list[list[float]]:
        # The free tier caps embeddings at 100 requests/minute; on a 429 wait out
        # the window (the built-in retry backoff is too short) and try again.
        for attempt in range(8):
            try:
                return (await embedder.embed(texts)).vectors
            except ProviderError as exc:
                msg = str(exc).lower()
                if "429" not in msg and "quota" not in msg:
                    raise
                print(
                    f"  rate-limited; waiting 20s to reset the per-minute cap "
                    f"(attempt {attempt + 1}/8)..."
                )
                await asyncio.sleep(20)
        raise RuntimeError("embedding still rate-limited after repeated waits")

    done = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start : start + BATCH]
        vectors = await _embed_batch([c.text for c in batch])
        ids, embeds, docs, metas = [], [], [], []
        for chunk, vec in zip(batch, vectors, strict=False):
            ids.append(str(chunk.id))
            embeds.append(vec)
            docs.append(chunk.text)
            metas.append(
                {
                    "org_id": str(chunk.org_id),
                    "doc_id": str(chunk.doc_id),
                    "category_key": chunk.category_key,
                    "retrieval_namespace": chunk.retrieval_namespace,
                    "doc_status": _status(chunk.doc_status),
                    "version": chunk.version,
                    "source_uri": chunk.source_uri or "",
                    "last_verified_at": (
                        chunk.last_verified_at.isoformat() if chunk.last_verified_at else None
                    ),
                    "embedding_model_id": embedder.model_id,
                }
            )
        await store.upsert(
            ids=ids, embeddings=embeds, documents=docs, metadatas=metas, collection=collection
        )
        done += len(batch)
        print(f"  indexed {done}/{len(rows)}")

    print(f"DONE — {done} chunks embedded into the '{collection}' vector index.")


if __name__ == "__main__":
    asyncio.run(main())
