"""Ingestion tests: parser dispatch, chunking, and the embed+upsert pipeline."""

from __future__ import annotations

import pytest
from app.core.exceptions import ValidationError
from app.providers.fakes import FakeEmbeddingProvider
from app.rag.ingestion import IngestionPipeline
from app.rag.parsers import parse_document
from app.rag.parsers.html_text_parser import parse_html, parse_text
from app.rag.vectorstore import VectorHit


class FakeVectorStore:
    def __init__(self) -> None:
        self.upserts: list[dict] = []

    async def query(self, *, embedding, k, where=None, collection=None):
        return [VectorHit(id="x", score=1.0, document="d")]

    async def upsert(self, *, ids, embeddings, documents, metadatas, collection=None):
        self.upserts.append(
            {
                "ids": ids,
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas,
                "collection": collection,
            }
        )

    async def delete(self, *, ids, collection=None):
        return None


def test_parse_text_and_html() -> None:
    assert parse_text(b"hello world") == "hello world"
    html = b"<html><head><style>x{}</style></head><body><p>Reset VPN</p></body></html>"
    assert "Reset VPN" in parse_html(html)
    assert "x{}" not in parse_html(html)


def test_parse_document_dispatch_by_content_type() -> None:
    assert parse_document(b"plain", content_type="text/plain") == "plain"
    with pytest.raises(ValidationError):
        parse_document(b"data", filename="mystery.xyz")


@pytest.mark.asyncio
async def test_ingestion_pipeline_chunks_embeds_and_upserts() -> None:
    store = FakeVectorStore()
    pipeline = IngestionPipeline(FakeEmbeddingProvider(dim=8), store, chunk_size=120, overlap=20)
    text = "To reset the VPN client, open GlobalProtect, sign out, then reconnect. " * 20
    result = await pipeline.index_text(
        org_id="org-1",
        doc_id="doc-1",
        text=text,
        namespace="vpn",
        category_key="vpn",
        collection="kb_chunks_pending",
    )
    assert result.chunk_count > 1
    assert len(result.chunk_ids) == result.chunk_count == len(result.chunks)
    assert store.upserts and store.upserts[0]["collection"] == "kb_chunks_pending"
    upsert = store.upserts[0]
    assert len(upsert["embeddings"]) == result.chunk_count
    assert len(upsert["embeddings"][0]) == 8
    assert upsert["metadatas"][0]["retrieval_namespace"] == "vpn"
    assert upsert["metadatas"][0]["doc_status"] == "pending_review"


@pytest.mark.asyncio
async def test_ingestion_index_document_parses_first() -> None:
    store = FakeVectorStore()
    pipeline = IngestionPipeline(FakeEmbeddingProvider(dim=8), store)
    result = await pipeline.index_document(
        data=b"VPN reset steps: open app, sign out, reconnect.",
        content_type="text/plain",
        org_id="o",
        doc_id="d",
        namespace="vpn",
        category_key="vpn",
        collection="kb_chunks_pending",
    )
    assert result.chunk_count >= 1
    assert store.upserts
