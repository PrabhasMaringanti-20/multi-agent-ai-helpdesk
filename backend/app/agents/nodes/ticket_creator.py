"""ticket_creator — assemble + persist an engineer-ready ticket (Phase 9)."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState
from app.core.constants import Decision, SensitivityLevel, TicketPriority


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _priority(state: AgentState) -> TicketPriority:
    if state["conversation"].sensitivity_level == SensitivityLevel.HIGH:
        return TicketPriority.HIGH
    return TicketPriority.MEDIUM


async def ticket_creator(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    conversation = state["conversation"]
    reason = state.get("escalation_reason") or "ai_unresolved"
    category = conversation.category or "application_error"
    org_id, conv_id, user_id = (
        _uuid(state["org_id"]),
        _uuid(state["thread_id"]),
        _uuid(state["user_id"]),
    )

    if deps.tickets is None or None in (org_id, conv_id, user_id):
        return {
            "escalation_reason": reason,
            "decision": Decision.ESCALATE,
            "tier": "L3",
            "node_path": ["ticket_creator"],
        }

    try:
        ticket = await deps.tickets.create_from_conversation(
            org_id=org_id,
            conversation_id=conv_id,
            created_by_user_id=user_id,
            category=category,
            subject=f"[{category}] {state['normalized_query'][:100]}",
            escalation_reason=reason,
            redacted_transcript={"messages": state.get("messages", [])},
            intake_fields=conversation.filled_slots,
            priority=_priority(state),
            final_confidence=state.get("final_confidence"),
        )
        ticket_id = str(ticket.id)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "escalation_reason": reason,
            "decision": Decision.ESCALATE,
            "tier": "L3",
            "node_path": ["ticket_creator"],
        }

    return {
        "ticket_id": ticket_id,
        "escalation_reason": reason,
        "decision": Decision.ESCALATE,
        "tier": "L3",
        "node_path": ["ticket_creator"],
        "audit_trail": [{"node": "ticket_creator", "ticket_id": ticket_id}],
    }


__all__ = ["ticket_creator"]
