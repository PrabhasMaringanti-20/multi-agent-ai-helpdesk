"""human_handoff — route to the engineer queue + gated notify (Escalation)."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


def _uuid(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def human_handoff(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    conversation = state["conversation"]
    queue = deps.categories.get(conversation.category).handoff_queue
    org_id = _uuid(state["org_id"])
    ticket_id = _uuid(state.get("ticket_id"))

    if deps.notifications is not None and org_id is not None and ticket_id is not None:
        # Notifying the engineer is best-effort: it must never fail the handoff.
        with contextlib.suppress(Exception):
            await deps.notifications.notify_engineer(
                org_id=org_id, ticket_id=ticket_id, queue=queue
            )

    approval = state["approval"].model_copy(update={"awaiting_human": True, "handoff_queue": queue})
    return {
        "approval": approval,
        "node_path": ["human_handoff"],
        "audit_trail": [
            {"node": "human_handoff", "queue": queue, "ticket_id": state.get("ticket_id")}
        ],
    }


__all__ = ["human_handoff"]
