"""confidence_gate — central deterministic router (Confidence Evaluation)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents import confidence
from app.agents.config_schema import get_deps
from app.agents.routing import decide_confidence
from app.agents.state import AgentState
from app.core.constants import Decision


async def confidence_gate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    conversation = state["conversation"]
    thresholds = deps.thresholds.for_category(conversation.category, conversation.sensitivity_level)
    citations = state.get("citations") or []
    answer = state.get("draft_answer") or ""

    report = confidence.evaluate(
        intent_confidence=conversation.intent_confidence,
        max_relevance_score=state["retrieval"].max_relevance_score,
        grounding_score=state.get("grounding_score"),
        contradiction=state.get("contradiction_flag", False),
        answer_text=answer,
        num_citations=len(citations),
    )
    citation_valid = report.citation_quality > 0 if citations else False
    answer_relevant = bool(answer.strip())

    decision = decide_confidence(
        state,
        thresholds,
        final_confidence=report.final_confidence,
        grounding_score=report.grounding_score,
        contradiction=report.contradiction,
        citation_valid=citation_valid,
        answer_relevant=answer_relevant,
    )

    updates: dict[str, Any] = {
        "final_confidence": report.final_confidence,
        "grounding_score": report.grounding_score,
        "decision": decision,
        "node_path": ["confidence_gate"],
        "audit_trail": [
            {
                "node": "confidence_gate",
                "decision": str(decision),
                "final_confidence": report.final_confidence,
                "hallucination_risk": report.hallucination_risk,
            }
        ],
    }
    if decision == Decision.RETRY_RETRIEVAL:
        updates["retry_count"] = state["retry_count"] + 1
    if decision == Decision.ESCALATE and not state.get("escalation_reason"):
        updates["escalation_reason"] = "contradiction" if report.contradiction else "low_confidence"
    return updates


__all__ = ["confidence_gate"]
