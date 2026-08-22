"""intent_classifier — small-tier LLM classification (Router)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, create_model

from app.agents.config_schema import get_deps
from app.agents.state import AgentState
from app.core.constants import SensitivityLevel
from app.providers.base import ChatMessage


class IntentResult(BaseModel):
    category: str = "application_error"
    intent: str = "support_request"
    intent_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    sensitivity_level: str = "low"
    control_intent: str | None = None


_SENSITIVITIES = {e.value for e in SensitivityLevel}


async def _extract_slots(
    deps: Any, required: list[str], prior_filled: dict[str, Any], text: str
) -> dict[str, str]:
    """Pull required intake fields the user has explicitly supplied in ``text``.

    Slot filling is what lets a clarified conversation advance past the
    clarifier into retrieval — without it, ``missing_slots`` never empties and
    every request eventually escalates. Only fields the user actually stated are
    returned; already-known fields are skipped to save a round-trip.
    """
    # ``issue_type`` is a guided-choice slot answered via quick-reply buttons, not
    # extracted from free text — leaving it unfilled lets a vague first turn show
    # the guided clarification with quick replies (#3).
    to_find = [s for s in required if not prior_filled.get(s) and s != "issue_type"]
    if not to_find:
        return {}
    model = create_model("SlotExtraction", **dict.fromkeys(to_find, (str | None, None)))
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You extract structured intake fields from an IT helpdesk user's "
                "message. Fill a field ONLY if the user explicitly provided that "
                "information; otherwise leave it null. Never guess or infer."
            ),
        ),
        ChatMessage(
            role="user",
            content=f"Fields to extract: {', '.join(to_find)}.\n\nUser message:\n{text}",
        ),
    ]
    try:
        extracted = await deps.llm_small.generate_structured(messages, model)
        data = extracted.model_dump() if hasattr(extracted, "model_dump") else dict(extracted)
    except Exception:  # noqa: BLE001 - extraction is best-effort; missing stays missing
        return {}
    return {k: str(v).strip() for k, v in data.items() if v and str(v).strip()}


async def intent_classifier(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    categories = ", ".join(deps.categories.keys())
    messages = deps.prompts.render(
        "router",
        categories=categories,
        required_slots="",
        summary=state["memory"].summary or "",
        input=state["normalized_query"],
    )
    try:
        result: IntentResult = await deps.llm_small.generate_structured(messages, IntentResult)
    except Exception:  # noqa: BLE001 - fall back to a safe default classification
        result = IntentResult()

    required = deps.categories.required_slots(result.category)
    prior_filled = dict(state["conversation"].filled_slots)
    newly = await _extract_slots(deps, required, prior_filled, state["normalized_query"])
    filled = {**prior_filled, **newly}
    missing = [slot for slot in required if not filled.get(slot)]
    sensitivity = (
        SensitivityLevel(result.sensitivity_level)
        if result.sensitivity_level in _SENSITIVITIES
        else SensitivityLevel.LOW
    )
    conversation = state["conversation"].model_copy(
        update={
            "category": result.category,
            "intent": result.intent,
            "intent_confidence": result.intent_confidence,
            "sensitivity_level": sensitivity,
            "control_intent": result.control_intent or state["conversation"].control_intent,
            "required_slots": required,
            "missing_slots": missing,
            "filled_slots": filled,
        }
    )
    return {
        "conversation": conversation,
        "intent_confidence": result.intent_confidence,
        "node_path": ["intent_classifier"],
    }


__all__ = ["intent_classifier", "IntentResult"]
