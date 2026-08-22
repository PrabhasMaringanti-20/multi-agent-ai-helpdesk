"""query_planner — coreference-resolve + rewrite the retrieval query (Retriever)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


async def query_planner(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    category = state["conversation"].category
    namespace = deps.categories.get(category).retrieval_namespace
    fallback = state["normalized_query"]

    try:
        messages = deps.prompts.render(
            "retriever",
            category=category or "",
            summary=state["memory"].summary or "",
            input=fallback,
        )
        result = await deps.llm_small.generate(messages)
        # Keep only the first non-empty line: the rewrite must be a single concise
        # query, and a stray preamble/blank line would pollute lexical reranking.
        lines = [ln.strip() for ln in result.text.splitlines() if ln.strip()]
        planned = (lines[0] if lines else fallback) or fallback
    except Exception:  # noqa: BLE001 - degrade to the raw query
        planned = fallback

    retrieval = state["retrieval"].model_copy(
        update={
            "query": planned,
            "namespace": namespace,
            "filters": {
                "org_id": state["org_id"],
                "retrieval_namespace": namespace,
                "category": category,
            },
        }
    )
    return {"retrieval": retrieval, "node_path": ["query_planner"]}


__all__ = ["query_planner"]
