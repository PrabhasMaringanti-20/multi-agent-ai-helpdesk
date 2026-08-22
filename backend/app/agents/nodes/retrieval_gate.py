"""retrieval_gate — reliability gate #1 (evidence sufficiency)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.routing import retrieval_is_sufficient
from app.agents.state import AgentState


async def retrieval_gate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    conversation = state["conversation"]
    thresholds = deps.thresholds.for_category(conversation.category, conversation.sensitivity_level)
    sufficient = retrieval_is_sufficient(state, thresholds)
    retrieval = state["retrieval"].model_copy(update={"sufficient": sufficient})
    return {
        "retrieval": retrieval,
        "node_path": ["retrieval_gate"],
        "audit_trail": [
            {
                "node": "retrieval_gate",
                "sufficient": sufficient,
                "max_score": retrieval.max_relevance_score,
            }
        ],
    }


__all__ = ["retrieval_gate"]
