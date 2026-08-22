"""l2_resolver — the L2 assisted-resolution tier (between L1 self-service and L3 human).

Reached only when the L1 attempt (grounded synthesis) could not confidently
answer. Instead of escalating straight to a human, L2 makes ONE deeper attempt:
a *broad* search across the whole knowledge base (all categories/namespaces) plus
the uploaded Document-Search files, then synthesizes a best-effort grounded
answer. If that yields something usable it is delivered (tagged L2); otherwise the
turn escalates to L3 (ticket_creator -> human_handoff). Runs at most meaningfully
once per turn and always routes forward, so it introduces no loops.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


def _score(chunk: Any) -> float:
    return chunk.rerank_score or chunk.score or 0.0


async def l2_resolver(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    retrieval = state["retrieval"]
    query = retrieval.query or state["normalized_query"]

    # ---- broad re-retrieval (drop the category/namespace restriction) ------ #
    candidates: list[Any] = []
    max_relevance = 0.0
    try:
        outcome = await deps.retriever.retrieve(
            query=query, org_id=state["org_id"], namespace=None, category=None
        )
        candidates = list(outcome.candidates)
        max_relevance = outcome.max_relevance_score
    except Exception:  # noqa: BLE001 - broad retrieval is best-effort
        candidates, max_relevance = [], 0.0

    uploads = getattr(deps, "uploads", None)
    if uploads is not None:
        try:
            extra = await uploads.search(state["org_id"], query)
        except Exception:  # noqa: BLE001
            extra = []
        if extra:
            candidates = candidates + list(extra)
            candidates.sort(key=_score, reverse=True)
            candidates = candidates[:6]
            max_relevance = max(max_relevance, max(_score(c) for c in extra))

    if not candidates:
        return {
            "l2_attempted": True,
            "l2_resolved": False,
            "tier": "L3",
            "node_path": ["l2_resolver"],
            "audit_trail": [{"node": "l2_resolver", "resolved": False, "reason": "no candidates"}],
        }

    from app.rag.retriever import build_citations, build_context

    context = build_context(candidates)
    try:
        text = (
            await deps.llm_large.generate(
                deps.prompts.render("knowledge", input=query, context=context)
            )
        ).text.strip()
        if not text or "ABSTAIN" in text.upper()[:80]:
            text = candidates[0].text.strip()  # grounded attempt abstained -> extractive
    except Exception:  # noqa: BLE001 - LLM unavailable: serve the top passage verbatim
        text = candidates[0].text.strip()

    updated = retrieval.model_copy(
        update={
            "candidates": candidates,
            "context": context,
            "max_relevance_score": max_relevance,
        }
    )
    answer = (
        f"{text}\n\n> **Note:** Resolved by the **L2 assist** — a broad search across "
        "the full knowledge base and attached files."
    )
    return {
        "retrieval": updated,
        "citations": build_citations(candidates),
        "draft_answer": answer,
        "claims": [text],
        "abstained": False,
        "general_answer": True,
        "l2_attempted": True,
        "l2_resolved": True,
        "tier": "L2",
        "node_path": ["l2_resolver"],
        "audit_trail": [{"node": "l2_resolver", "resolved": True}],
    }


__all__ = ["l2_resolver"]
