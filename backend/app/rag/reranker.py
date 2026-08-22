"""Reranking: blend fused score with lexical relevance + KB relevance signals.

Default is a deterministic heuristic (no heavy model) that combines the RRF
fused score, query/chunk lexical overlap, and any ``boost``/``quarantine``
metadata carried from ``relevance_signals``. This is dependency-free and
testable; a cross-encoder can be layered in later without changing the contract.
"""

from __future__ import annotations

from app.agents.state import RetrievedChunk


def _lexical_overlap(query: str, text: str) -> float:
    q = {t.lower() for t in query.split() if len(t) > 2}
    if not q:
        return 0.0
    d = {t.lower() for t in text.split() if len(t) > 2}
    return len(q & d) / len(q)


class HeuristicReranker:
    def __init__(self, *, fused_weight: float = 0.6, lexical_weight: float = 0.4) -> None:
        self._fused_weight = fused_weight
        self._lexical_weight = lexical_weight

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], *, top_k: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        max_fused = max((c.score for c in candidates), default=1.0) or 1.0
        scored: list[RetrievedChunk] = []
        for chunk in candidates:
            if chunk.metadata.get("is_quarantined"):
                continue
            normalized_fused = chunk.score / max_fused
            lexical = _lexical_overlap(query, chunk.text)
            boost = float(chunk.metadata.get("boost_factor", 1.0) or 1.0)
            rerank_score = (
                self._fused_weight * normalized_fused + self._lexical_weight * lexical
            ) * boost
            scored.append(chunk.model_copy(update={"rerank_score": round(rerank_score, 6)}))
        scored.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
        return scored[:top_k]


__all__ = ["HeuristicReranker"]
