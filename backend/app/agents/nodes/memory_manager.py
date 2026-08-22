"""memory_manager — dual-mode: load memory (pre) then persist memory (post)."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def memory_manager(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    user_id = _uuid(state["user_id"])
    conversation_id = _uuid(state["thread_id"])
    org_id = _uuid(state["org_id"])

    # Persist mode: reached after the responder has produced a reply.
    if state.get("response_text") is not None:
        if deps.memory is None or None in (user_id, conversation_id, org_id):
            return {"node_path": ["memory_manager:persist"]}
        try:
            updated = await deps.memory.persist_turn(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=state["turn_id"],
                state=state["memory"],
            )
            return {"memory": updated, "node_path": ["memory_manager:persist"]}
        except Exception as exc:  # noqa: BLE001 - memory maintenance is best-effort
            return {
                "node_path": ["memory_manager:persist"],
                "audit_trail": [{"node": "memory_manager", "error": str(exc)}],
            }

    # Load mode: hydrate window/summary/facts before classification.
    if deps.memory is None or None in (user_id, conversation_id):
        return {"node_path": ["memory_manager:load"]}
    try:
        loaded = await deps.memory.load_state(user_id=user_id, conversation_id=conversation_id)
        return {"memory": loaded, "node_path": ["memory_manager:load"]}
    except Exception as exc:  # noqa: BLE001
        return {
            "node_path": ["memory_manager:load"],
            "audit_trail": [{"node": "memory_manager", "error": str(exc)}],
        }


__all__ = ["memory_manager"]
