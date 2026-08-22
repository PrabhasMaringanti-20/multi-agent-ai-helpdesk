"""Knowledge-base service: semantic-search facade over the hybrid retriever."""

from __future__ import annotations

import uuid

from app.rag.retriever import HybridRetriever, RetrievalOutcome
from app.repositories.kb_repo import KnowledgeRepository


class KbService:
    def __init__(self, retriever: HybridRetriever, kb_repo: KnowledgeRepository) -> None:
        self._retriever = retriever
        self._kb = kb_repo

    async def semantic_search(
        self,
        *,
        query: str,
        org_id: uuid.UUID | str,
        namespace: str | None = None,
        category: str | None = None,
    ) -> RetrievalOutcome:
        return await self._retriever.retrieve(
            query=query, org_id=str(org_id), namespace=namespace, category=category
        )

    async def get_document(self, doc_id: uuid.UUID, org_id: uuid.UUID):
        return await self._kb.get_document(doc_id, org_id)


__all__ = ["KbService"]
