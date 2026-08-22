"""solution_synthesizer — grounded cited answer, with a general-answer fallback.

1. If retrieval is strong, answer STRICTLY from the sources (grounded + cited).
2. If retrieval is weak OR the grounded attempt abstains (sources don't actually
   answer it), give a best-effort GENERAL LLM answer (clearly flagged) instead of
   escalating. Only a genuine LLM failure / empty result abstains -> escalates.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


def _is_abstain(text: str | None) -> bool:
    if not text:
        return True
    head = text.strip().upper()[:80]
    return "ABSTAIN" in head


def _extractive(
    retrieval: Any, *, general: bool, error: str | None = None
) -> dict[str, Any] | None:
    """Serve the top retrieved passage verbatim when the LLM is unavailable.

    Returns ``None`` if nothing was retrieved (so the caller escalates). When
    ``general`` is True the answer is delivered via the general-answer gate
    (used as the last resort so a quota outage still yields the best KB match
    instead of a ticket).
    """
    if not getattr(retrieval, "candidates", None):
        return None
    top = retrieval.candidates[0].text.strip()
    result: dict[str, Any] = {
        "draft_answer": (
            "Here's the most relevant information from our knowledge base [1]:\n\n"
            f"{top}\n\n> **Note:** Served directly from the matching source "
            "(AI summarization was temporarily unavailable)."
        ),
        "claims": [retrieval.candidates[0].text],
        "abstained": False,
        "general_answer": general,
        "node_path": ["solution_synthesizer"],
        "audit_trail": [{"node": "solution_synthesizer", "llm_fallback": True}],
    }
    if error:
        result["error"] = error
    return result


async def solution_synthesizer(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    retrieval = state["retrieval"]
    query = state["normalized_query"]

    # ---- 1) grounded attempt (only when retrieval is strong) -------------- #
    if retrieval.sufficient:
        messages = deps.prompts.render("knowledge", input=query, context=retrieval.context or "")
        try:
            text = (await deps.llm_large.generate(messages)).text.strip()
        except Exception as exc:  # noqa: BLE001 - LLM unavailable (quota/429)
            # Extractive fallback: serve the top matching passage verbatim.
            if retrieval.max_relevance_score >= 0.6:
                fallback = _extractive(retrieval, general=False, error=str(exc))
                if fallback is not None:
                    return fallback
            text = None
        if not _is_abstain(text):
            return {
                "draft_answer": text,
                "claims": [text],
                "abstained": False,
                "general_answer": False,
                "node_path": ["solution_synthesizer"],
            }
        # grounded attempt abstained -> fall through to a general answer

    # ---- 2) general best-effort answer ------------------------------------ #
    try:
        gtext = (
            await deps.llm_large.generate(deps.prompts.render("general", input=query))
        ).text.strip()
    except Exception as exc:  # noqa: BLE001 - LLM down: degrade, only then escalate
        # Last resort: if we retrieved anything at all, serve the best passage
        # (delivered via the general-answer gate) instead of raising a ticket.
        fallback = _extractive(retrieval, general=True, error=str(exc))
        if fallback is not None:
            return fallback
        return {"abstained": True, "error": str(exc), "node_path": ["solution_synthesizer"]}
    if _is_abstain(gtext):
        return {"abstained": True, "node_path": ["solution_synthesizer"]}
    return {
        "draft_answer": gtext,
        "claims": [gtext],
        "abstained": False,
        "general_answer": True,
        "node_path": ["solution_synthesizer"],
    }


__all__ = ["solution_synthesizer"]
