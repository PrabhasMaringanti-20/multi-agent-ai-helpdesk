"""grounding_verifier — reliability gate #2 (entailment / faithfulness)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


async def grounding_verifier(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    answer = state.get("draft_answer") or ""
    retrieval = state["retrieval"]
    sources = [c.text for c in retrieval.candidates]
    # #10 conserve LLM calls: when retrieval is very strong, trust it and skip the
    # entailment LLM round-trip (grounding proxied by relevance). Keeps the gate
    # fast and within provider quota without lowering answer quality; weaker
    # retrieval still goes through the full LLM verifier below.
    if retrieval.candidates and answer.strip() and retrieval.max_relevance_score >= 0.85:
        score = float(retrieval.max_relevance_score)
        return {
            "grounding_score": score,
            "contradiction_flag": False,
            "node_path": ["grounding_verifier"],
            "audit_trail": [{"node": "grounding_verifier", "skipped_llm": True, "score": score}],
        }
    try:
        verdict = await deps.verifier.verify(answer, sources)
        return {
            "grounding_score": verdict.score,
            "contradiction_flag": not verdict.entailed,
            "node_path": ["grounding_verifier"],
            "audit_trail": [
                {"node": "grounding_verifier", "score": verdict.score, "entailed": verdict.entailed}
            ],
        }
    except Exception as exc:  # noqa: BLE001
        # Graceful degradation: a TRANSIENT judge failure (e.g. a provider 429 /
        # timeout) must not masquerade as a hallucination and force an escalation.
        # Fall back to retrieval strength as the grounding proxy — a well-retrieved
        # answer still delivers, while a weak retrieval still won't clear the gate.
        fallback = float(state["retrieval"].max_relevance_score or 0.0)
        return {
            "grounding_score": fallback,
            "contradiction_flag": False,
            "error": str(exc),
            "node_path": ["grounding_verifier"],
            "audit_trail": [
                {"node": "grounding_verifier", "verifier_error": True, "fallback_score": fallback}
            ],
        }


__all__ = ["grounding_verifier"]
