"""info_collector — registry-driven batched clarification (Clarifier)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState
from app.core.constants import Decision

# Curated quick-reply options per category so a clarification turn can offer
# clickable choices (guided troubleshooting, #3). No extra LLM call — keeps the
# turn fast and within provider quota. Falls back to a generic set.
_QUICK_REPLIES: dict[str, list[str]] = {
    "outlook": [
        "Cannot log in",
        "Outlook keeps crashing",
        "Email not syncing",
        "Search not working",
        "Calendar issue",
        "Other",
    ],
    "teams": [
        "Cannot sign in",
        "No audio or video",
        "Messages won't send",
        "Screen share fails",
        "Other",
    ],
    "wifi": [
        "Can't connect at all",
        "Keeps dropping",
        "Connected but no internet",
        "Very slow",
        "Other",
    ],
    "printer": [
        "Nothing prints",
        "Paper jam or error light",
        "Can't find the printer",
        "Poor print quality",
        "Other",
    ],
    "browser": [
        "Pages won't load",
        "Browser is very slow",
        "Certificate/security warning",
        "Downloads blocked",
        "Other",
    ],
}
_GENERIC_QUICK = [
    "I can't log in",
    "It shows an error",
    "It's very slow",
    "It crashes",
    "Something else",
]


def _quick_replies(category: str | None) -> list[str]:
    return _QUICK_REPLIES.get(category or "", _GENERIC_QUICK)


async def info_collector(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    conversation = state["conversation"]
    rounds = state["clarification_rounds"] + 1

    try:
        messages = deps.prompts.render(
            "clarification",
            category=conversation.category or "",
            missing_slots=", ".join(conversation.missing_slots),
            filled_slots=str(conversation.filled_slots),
            input=state["normalized_query"],
        )
        result = await deps.llm_small.generate(messages)
        question = result.text.strip()
    except Exception:  # noqa: BLE001 - fall back to a templated question
        question = (
            "Could you share a bit more detail — specifically: "
            + ", ".join(conversation.missing_slots)
            + "?"
        )

    return {
        "clarification_rounds": rounds,
        "draft_answer": question,
        "decision": Decision.CLARIFY,
        "quick_replies": _quick_replies(conversation.category),
        "node_path": ["info_collector"],
    }


__all__ = ["info_collector"]
