"""Reciprocal Rank Fusion (RRF) for combining dense + sparse result lists."""

from __future__ import annotations

from collections.abc import Sequence

from app.agents.state import RetrievedChunk


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievedChunk]], *, k: int = 60
) -> list[RetrievedChunk]:
    """Fuse ranked candidate lists by RRF, de-duplicating on ``chunk_id``.

    Each chunk's fused ``score`` is the sum of ``1/(k + rank)`` across the lists
    it appears in. Per-modality scores (dense/sparse) are preserved on the merged
    chunk so the reranker can use them.
    """
    fused_scores: dict[str, float] = {}
    merged: dict[str, RetrievedChunk] = {}

    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked):
            fused_scores[chunk.chunk_id] = fused_scores.get(chunk.chunk_id, 0.0) + 1.0 / (
                k + rank + 1
            )
            if chunk.chunk_id not in merged:
                merged[chunk.chunk_id] = chunk
            else:
                existing = merged[chunk.chunk_id]
                merged[chunk.chunk_id] = existing.model_copy(
                    update={
                        "dense_score": existing.dense_score or chunk.dense_score,
                        "sparse_score": existing.sparse_score or chunk.sparse_score,
                    }
                )

    fused = [merged[cid].model_copy(update={"score": score}) for cid, score in fused_scores.items()]
    fused.sort(key=lambda c: c.score, reverse=True)
    return fused


__all__ = ["reciprocal_rank_fusion"]
