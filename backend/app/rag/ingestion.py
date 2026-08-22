"""KB ingestion pipeline: parse -> chunk -> embed -> upsert to the vector store.

This is the vector-indexing half of ingestion (dense side). Row persistence
(``kb_documents`` / ``kb_chunks`` for the sparse/BM25 side) is performed by the
ingestion worker task, which calls this pipeline and writes the returned chunks
to Postgres. The pipeline depends only on the ``EmbeddingProvider`` and
``VectorStore`` abstractions, so it is fully testable with fakes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.constants import DocStatus
from app.providers.base import EmbeddingProvider
from app.rag.chunker import chunk_text
from app.rag.parsers import parse_document
from app.rag.vectorstore import VectorStore


@dataclass
class IngestionResult:
    doc_id: str
    chunk_ids: list[str] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    chunk_count: int = 0
    embedding_model_id: str = ""


class IngestionPipeline:
    def __init__(
        self,
        embedder: EmbeddingProvider,
        vectorstore: VectorStore,
        *,
        chunk_size: int = 800,
        overlap: int = 120,
    ) -> None:
        self._embedder = embedder
        self._vectorstore = vectorstore
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def index_text(
        self,
        *,
        org_id: str,
        doc_id: str,
        text: str,
        namespace: str,
        category_key: str,
        collection: str,
        source_uri: str | None = None,
        version: int = 1,
        doc_status: DocStatus = DocStatus.PENDING_REVIEW,
    ) -> IngestionResult:
        chunks = chunk_text(text, chunk_size=self._chunk_size, overlap=self._overlap)
        if not chunks:
            return IngestionResult(doc_id=doc_id, embedding_model_id=self._embedder.model_id)

        embeddings = (await self._embedder.embed(chunks)).vectors
        chunk_ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas: list[dict[str, Any]] = [
            {
                "org_id": str(org_id),
                "doc_id": str(doc_id),
                "category_key": category_key,
                "retrieval_namespace": namespace,
                "doc_status": doc_status.value,
                "chunk_index": index,
                "version": version,
                "source_uri": source_uri or "",
                "embedding_model_id": self._embedder.model_id,
            }
            for index in range(len(chunks))
        ]
        await self._vectorstore.upsert(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
            collection=collection,
        )
        return IngestionResult(
            doc_id=doc_id,
            chunk_ids=chunk_ids,
            chunks=chunks,
            chunk_count=len(chunks),
            embedding_model_id=self._embedder.model_id,
        )

    async def index_document(
        self,
        *,
        data: bytes,
        filename: str | None = None,
        content_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        text = parse_document(data, filename=filename, content_type=content_type)
        return await self.index_text(text=text, **kwargs)


__all__ = ["IngestionPipeline", "IngestionResult"]
