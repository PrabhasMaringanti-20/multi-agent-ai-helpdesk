"""Hybrid retrieval orchestration: dense + sparse -> RRF -> rerank -> context.

Also builds the citation list, the numbered context block for grounded
generation, metadata filters, and top-K selection. Dense/sparse searchers are
injected as ``Searcher`` implementations so the orchestrator can run against
real backends or fakes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.agents.state import Citation, RetrievedChunk
from app.core.logging import get_logger
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.reranker import HeuristicReranker

_logger = get_logger(__name__)

# Process-local retrieval cache (perf, §10). Same (tenant, namespace, category,
# query) returns identical candidates, so caching is safe and never changes the
# answer. Short TTL keeps it fresh against KB edits; bounded to cap memory.
_RETRIEVAL_TTL = 300.0
_RETRIEVAL_MAX = 512
_retrieval_cache: dict[tuple[str, str, str, str], tuple[float, RetrievalOutcome]] = {}


@runtime_checkable
class Searcher(Protocol):
    async def search(
        self, query: str, *, filters: dict[str, Any], k: int
    ) -> list[RetrievedChunk]: ...


class RetrievalOutcome(BaseModel):
    candidates: list[RetrievedChunk] = Field(default_factory=list)
    context: str = ""
    citations: list[Citation] = Field(default_factory=list)
    max_relevance_score: float = 0.0


def build_context(candidates: list[RetrievedChunk]) -> str:
    return "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(candidates))


def build_citations(candidates: list[RetrievedChunk]) -> list[Citation]:
    return [
        Citation(
            chunk_id=c.chunk_id,
            doc_id=c.doc_id,
            source_uri=c.source_uri,
            version=c.version,
        )
        for c in candidates
    ]


class HybridRetriever:
    def __init__(
        self,
        dense: Searcher,
        sparse: Searcher,
        reranker: HeuristicReranker | None = None,
        *,
        top_k: int = 6,
        candidate_k: int = 20,
    ) -> None:
        self._dense = dense
        self._sparse = sparse
        self._reranker = reranker or HeuristicReranker()
        self._top_k = top_k
        self._candidate_k = candidate_k

    async def _safe_search(
        self, searcher: Searcher, query: str, filters: dict[str, Any]
    ) -> list[RetrievedChunk]:
        try:
            return await searcher.search(query, filters=filters, k=self._candidate_k)
        except Exception as exc:  # noqa: BLE001 - one modality may fail; degrade
            _logger.warning("%s failed: %s", type(searcher).__name__, exc)
            return []

    async def retrieve(
        self,
        *,
        query: str,
        org_id: str,
        namespace: str | None = None,
        category: str | None = None,
        extra_filters: dict[str, Any] | None = None,
    ) -> RetrievalOutcome:
        filters: dict[str, Any] = {
            "org_id": org_id,
            "retrieval_namespace": namespace,
            "category": category,
        }
        if extra_filters:
            filters.update(extra_filters)

        # Cache lookup (only for the common, filter-free query path).
        cache_key = (
            str(org_id),
            str(namespace or ""),
            str(category or ""),
            (query or "").strip().lower(),
        )
        if not extra_filters and cache_key[3]:
            hit = _retrieval_cache.get(cache_key)
            if hit is not None and (time.monotonic() - hit[0]) < _RETRIEVAL_TTL:
                return hit[1]

        dense_hits, sparse_hits = await asyncio.gather(
            self._safe_search(self._dense, query, filters),
            self._safe_search(self._sparse, query, filters),
        )
        fused = reciprocal_rank_fusion([dense_hits, sparse_hits])
        ranked = await self._reranker.rerank(query, fused, top_k=self._top_k)
        max_score = ranked[0].rerank_score or 0.0 if ranked else 0.0
        outcome = RetrievalOutcome(
            candidates=ranked,
            context=build_context(ranked),
            citations=build_citations(ranked),
            max_relevance_score=max_score,
        )
        if not extra_filters and cache_key[3]:
            if len(_retrieval_cache) >= _RETRIEVAL_MAX:
                _retrieval_cache.clear()
            _retrieval_cache[cache_key] = (time.monotonic(), outcome)
        return outcome


__all__ = [
    "Searcher",
    "RetrievalOutcome",
    "HybridRetriever",
    "build_context",
    "build_citations",
]
