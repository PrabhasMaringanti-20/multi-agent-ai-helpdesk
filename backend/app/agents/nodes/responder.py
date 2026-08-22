"""responder — single egress: compose reply, emit analytics + audit."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState
from app.core.constants import ActorType, Decision


def _uuid(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _compose(state: AgentState) -> str:
    if state.get("cache_hit") and state.get("cached_answer"):
        return str(state["cached_answer"])
    control = state["conversation"].control_intent
    if control == "greeting":
        return "Hello! I'm the IT helpdesk assistant. What can I help you with today?"
    if control == "cancel":
        return "No problem — I've cancelled that. Let me know if there's anything else."
    if state["approval"].awaiting_human or state.get("ticket_id"):
        ticket = state.get("ticket_id")
        suffix = f" (ticket {ticket})" if ticket else ""
        return (
            "I wasn't able to fully resolve this automatically, so I've created a "
            f"support ticket{suffix} and routed it to a human engineer who will follow up."
        )
    if state.get("decision") == Decision.CLARIFY and state.get("draft_answer"):
        return str(state["draft_answer"])
    if state.get("draft_answer"):
        return str(state["draft_answer"])
    return "I'm sorry, I couldn't find a reliable answer to that."


def _event_type(state: AgentState) -> str:
    decision = state.get("decision")
    if decision == Decision.DELIVER:
        return "auto_resolved"
    if decision == Decision.CLARIFY:
        return "clarification_requested"
    if decision == Decision.ESCALATE or state.get("ticket_id"):
        return "escalated"
    return "chat_answered"


async def responder(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    text = _compose(state)
    org_id = _uuid(state["org_id"])

    if deps.analytics is not None and org_id is not None:
        # Analytics is best-effort: never fail a reply over a metrics write.
        with contextlib.suppress(Exception):
            await deps.analytics.record(
                org_id=org_id,
                event_type=_event_type(state),
                user_id=_uuid(state["user_id"]),
                conversation_id=_uuid(state["thread_id"]),
                ticket_id=_uuid(state.get("ticket_id")),
                category=state["conversation"].category,
                properties={"decision": str(state.get("decision"))},
            )
    if deps.audit is not None and org_id is not None:
        # Audit is best-effort here; the request must still complete.
        with contextlib.suppress(Exception):
            await deps.audit.record(
                org_id=org_id,
                action=f"chat.{_event_type(state)}",
                resource_type="conversation",
                actor_type=ActorType.AGENT,
                actor_user_id=_uuid(state["user_id"]),
                resource_id=_uuid(state["thread_id"]),
            )

    return {
        "response_text": text,
        "messages": [{"role": "assistant", "content": text}],
        "node_path": ["responder"],
    }


__all__ = ["responder"]
