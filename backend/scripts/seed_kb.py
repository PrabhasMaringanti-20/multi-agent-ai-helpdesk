"""Idempotent KB-only seed: publish the 93 demo articles and embed them.

Unlike scripts/seed_demo.py (which also creates demo tickets, notifications,
analytics events and chat history), this touches only the knowledge base:
it publishes every article in demo_kb_data.py as a KbDocument + KbChunk
(skipping titles that already exist), then embeds every unindexed published
chunk into the configured vector store (VECTOR_STORE_BACKEND, "pg" by
default) so retrieval has real semantic vectors to search.

Requires the 'acme' org + an admin user to already exist (bootstrap_admin.py
creates them). Gated by SEED_KB_DEMO in the entrypoint, same pattern as
BOOTSTRAP_DEMO -> bootstrap_admin.py.

Run from backend/ with the app importable:
    PYTHONPATH=backend python scripts/seed_kb.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))  # for `from demo_kb_data import KB_ARTICLES`

from app.core.config import get_settings  # noqa: E402
from app.core.constants import DocStatus, SourceType  # noqa: E402
from app.core.exceptions import ProviderError  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionFactory, dispose_engine, engine  # noqa: E402
from app.models.knowledge import KbChunk, KbDocument  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.rag_vector import RagVector  # noqa: E402
from app.models.registry import CategoryRegistry as CategoryRow  # noqa: E402
from app.models.user import User  # noqa: E402
from app.providers.registry import get_embedding_provider  # noqa: E402
from app.rag.vectorstore import get_vector_store  # noqa: E402
from app.registries.category_registry import get_category_registry  # noqa: E402
from demo_kb_data import KB_ARTICLES  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

NOW = datetime.now(UTC)
EMBED_BATCH = 10  # chunks per embed() call - stays under free-tier rate limits


def _article_body(a: dict) -> str:
    """Compose one rich markdown chunk carrying every article field."""
    sym = "\n".join(f"- {s}" for s in a.get("symptoms", []))
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(a.get("steps", []), 1))
    rel = "\n".join(f"- {r}" for r in a.get("related_articles", []))
    perms = ", ".join(a.get("required_permissions", [])) or "Self-service"
    tags = ", ".join(a.get("tags", []))
    kw = ", ".join(a.get("confidence_keywords", []))
    return (
        f"# {a['title']}\n\n"
        f"**Category:** {a['category_key']}  |  **Estimated resolution time:** "
        f"{a.get('est_resolution_time', 'N/A')}  |  **Required permissions:** {perms}\n\n"
        f"## Problem\n{a['problem']}\n\n"
        f"## Symptoms\n{sym}\n\n"
        f"## Root Cause\n{a['root_cause']}\n\n"
        f"## Step-by-step Guided Solution\n{steps}\n\n"
        f"## Related Articles\n{rel}\n\n"
        f"{a.get('screenshot_placeholder', '')}\n\n"
        f"**Tags:** {tags}\n\n**Keywords:** {kw}\n"
    )


async def _ensure_categories(s: AsyncSession) -> None:
    """Insert any category_registry rows the migration didn't seed.

    The Alembic migration only seeds the 8 canonical categories; demo_kb_data's
    93 articles span 30 category keys (the other 22 are the CategoryRegistry's
    "extended" set). kb_documents/kb_chunks FK into category_registry, so those
    rows must exist first or every non-canonical article fails to insert.
    """
    registry = get_category_registry()
    existing = {r for (r,) in (await s.execute(select(CategoryRow.category_key))).all()}
    added = 0
    for key in registry:
        if key in existing:
            continue
        c = registry.get(key)
        s.add(
            CategoryRow(
                category_key=c.category_key,
                display_name=c.display_name,
                required_intake_fields=dict(c.required_intake_fields),
                retrieval_namespace=c.retrieval_namespace,
                sla_tier=c.sla_tier,
                handoff_queue=c.handoff_queue,
                thresholds=dict(c.thresholds),
                tool_bindings={},
                is_active=True,
            )
        )
        added += 1
    await s.flush()
    if added:
        print(f"[seed_kb] added {added} category_registry row(s)")


async def _publish_articles() -> int:
    org_slug = os.getenv("BOOTSTRAP_ORG_SLUG", "acme")
    async with SessionFactory() as s:
        await _ensure_categories(s)
        org = (
            await s.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one_or_none()
        if org is None:
            raise RuntimeError(f"org '{org_slug}' not found - run bootstrap_admin.py first")
        admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@acme.com")
        admin = (
            await s.execute(
                select(User).where(User.org_id == org.id, User.email == admin_email)
            )
        ).scalar_one_or_none()
        if admin is None:
            raise RuntimeError(
                f"user '{admin_email}' not found in org '{org_slug}' - run bootstrap_admin.py first"
            )

        existing_titles = {
            t
            for (t,) in (
                await s.execute(select(KbDocument.title).where(KbDocument.org_id == org.id))
            ).all()
        }
        added = 0
        for a in KB_ARTICLES:
            if a["title"] in existing_titles:
                continue
            body = _article_body(a)
            doc = KbDocument(
                org_id=org.id,
                title=a["title"],
                source_type=SourceType.MANUAL,
                category=a["category_key"],
                retrieval_namespace=a["category_key"],
                doc_status=DocStatus.PUBLISHED,
                version=1,
                checksum=hashlib.sha256(body.encode()).hexdigest(),
                created_by_user_id=admin.id,
                source_uri=f"seed://{a['category_key']}/{a['title']}",
                last_verified_at=NOW,
            )
            s.add(doc)
            await s.flush()
            s.add(
                KbChunk(
                    doc_id=doc.id,
                    org_id=org.id,
                    category_key=a["category_key"],
                    retrieval_namespace=a["category_key"],
                    chunk_index=0,
                    text=body,
                    embedding_model_id="seed",
                    doc_status=DocStatus.PUBLISHED,
                    version=1,
                    token_count=len(body.split()),
                    source_uri=doc.source_uri,
                    last_verified_at=NOW,
                )
            )
            added += 1
        await s.commit()
        print(f"[seed_kb] published {added} new article(s); {len(existing_titles)} already existed")
        return added


async def _embed_unindexed() -> int:
    settings = get_settings()
    collection = settings.CHROMA_KB_COLLECTION

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as session:
        rows = list(
            (
                await session.execute(select(KbChunk).where(KbChunk.doc_status == "published"))
            )
            .scalars()
            .all()
        )
        indexed = set(
            (
                await session.execute(
                    select(RagVector.id).where(RagVector.collection == collection)
                )
            )
            .scalars()
            .all()
        )
    rows = [c for c in rows if str(c.id) not in indexed]
    if not rows:
        print("[seed_kb] vector index already complete - nothing to embed")
        return 0

    embedder = get_embedding_provider()
    store = get_vector_store()
    print(
        f"[seed_kb] embedding {len(rows)} chunk(s) with {embedder.model_id} "
        f"(dim={embedder.dim}) into '{collection}' via {type(store).__name__} ..."
    )

    async def _embed_batch(texts: list[str]) -> list[list[float]]:
        for attempt in range(8):
            try:
                return (await embedder.embed(texts)).vectors
            except ProviderError as exc:
                msg = str(exc).lower()
                if "429" not in msg and "quota" not in msg:
                    raise
                print(f"[seed_kb]  rate-limited; waiting 20s (attempt {attempt + 1}/8)...")
                await asyncio.sleep(20)
        raise RuntimeError("embedding still rate-limited after repeated waits")

    done = 0
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
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
                    "doc_status": "published",
                    "version": chunk.version,
                    "source_uri": chunk.source_uri or "",
                    "embedding_model_id": embedder.model_id,
                }
            )
        await store.upsert(
            ids=ids, embeddings=embeds, documents=docs, metadatas=metas, collection=collection
        )
        done += len(batch)
        print(f"[seed_kb]   indexed {done}/{len(rows)}")
    return done


async def _run() -> None:
    added = await _publish_articles()
    embedded = await _embed_unindexed()
    await dispose_engine()
    print(f"[seed_kb] DONE - {added} article(s) published, {embedded} chunk(s) embedded")


if __name__ == "__main__":
    asyncio.run(_run())
