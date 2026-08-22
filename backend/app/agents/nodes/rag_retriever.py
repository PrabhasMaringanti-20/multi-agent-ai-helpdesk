"""rag_retriever — hybrid retrieval producing candidates/context/citations."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


async def rag_retriever(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    retrieval = state["retrieval"]
    query = retrieval.query or state["normalized_query"]
    try:
        outcome = await deps.retriever.retrieve(
            query=query,
            org_id=state["org_id"],
            namespace=retrieval.namespace,
            category=state["conversation"].category,
        )
    except Exception as exc:  # noqa: BLE001 - retrieval failure degrades to escalation
        return {
            "error": str(exc),
            "node_path": ["rag_retriever"],
            "audit_trail": [{"node": "rag_retriever", "error": str(exc)}],
        }

    candidates = list(outcome.candidates)
    max_relevance = outcome.max_relevance_score
    # Also search documents attached via Document Search (org-wide), so the chat
    # answers users from files an engineer/admin uploaded.
    uploads = getattr(deps, "uploads", None)
    if uploads is not None:
        extra = await uploads.search(state["org_id"], query)
        if extra:
            candidates = candidates + list(extra)
            candidates.sort(key=lambda c: c.rerank_score or c.score or 0.0, reverse=True)
            candidates = candidates[: max(len(outcome.candidates), 6)]
            max_relevance = max(
                max_relevance, max((c.rerank_score or c.score or 0.0) for c in extra)
            )

    from app.rag.retriever import build_citations, build_context

    updated = retrieval.model_copy(
        update={
            "candidates": candidates,
            "context": build_context(candidates),
            "max_relevance_score": max_relevance,
        }
    )
    return {
        "retrieval": updated,
        "citations": build_citations(candidates),
        "node_path": ["rag_retriever"],
    }


__all__ = ["rag_retriever"]
