"""Ingestion worker tasks (Phase 7): parse+chunk+embed+upsert a KB document.

Runs the async ingestion pipeline inside a fresh event loop + DB session. On a
live deployment this persists ``kb_documents`` + ``kb_chunks`` (sparse side) and
upserts vectors into the ChromaDB pending collection (dense side); an SME/admin
approval then promotes them to the published collection.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

from app.core.config import get_settings
from app.core.constants import DocStatus, SourceType
from app.db.session import SessionFactory
from app.providers.registry import get_embedding_provider
from app.rag.ingestion import IngestionPipeline
from app.rag.vectorstore import get_vector_store
from app.repositories.kb_repo import KnowledgeRepository
from app.workers.queue import BaseTask, celery_app


async def _ingest(
    *,
    org_id: str,
    created_by_user_id: str,
    title: str,
    category: str,
    namespace: str,
    text: str,
    source_uri: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    async with SessionFactory() as session:
        kb = KnowledgeRepository(session)
        document = await kb.create(
            org_id=uuid.UUID(org_id),
            title=title,
            source_type=SourceType.ADMIN_UPLOAD,
            category=category,
            retrieval_namespace=namespace,
            doc_status=DocStatus.PENDING_REVIEW,
            version=1,
            checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            created_by_user_id=uuid.UUID(created_by_user_id),
            source_uri=source_uri,
        )
        pipeline = IngestionPipeline(get_embedding_provider(), get_vector_store())
        result = await pipeline.index_text(
            org_id=org_id,
            doc_id=str(document.id),
            text=text,
            namespace=namespace,
            category_key=category,
            collection=settings.CHROMA_KB_PENDING_COLLECTION,
            source_uri=source_uri,
        )
        for index, chunk_id in enumerate(result.chunk_ids):
            await kb.add_chunk(
                chunk_id=chunk_id,
                doc_id=document.id,
                org_id=uuid.UUID(org_id),
                category_key=category,
                retrieval_namespace=namespace,
                chunk_index=index,
                text=result.chunks[index],
                embedding_model_id=result.embedding_model_id,
                source_uri=source_uri,
            )
        await session.commit()
        return {"doc_id": str(document.id), "chunk_count": result.chunk_count}


@celery_app.task(base=BaseTask, name="app.workers.ingest_document")
def ingest_document(**payload: Any) -> dict[str, Any]:
    """Celery entrypoint for asynchronous KB document ingestion."""
    return asyncio.run(_ingest(**payload))


__all__ = ["ingest_document"]
