"""Tool implementations (Phase 7). Every tool delegates to an existing service.

Tools are plain async callables ``handler(deps, **kwargs) -> dict`` plus a
``Tool`` descriptor (name + description + schema hint) so they can be bound to
agents via the tool registry and, later, exposed to LLM tool-calling. They never
touch the ORM directly — only ``GraphDeps`` services/repositories.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.config_schema import GraphDeps
from app.core.constants import NotificationType, TicketPriority

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler
    args: tuple[str, ...] = field(default_factory=tuple)

    async def __call__(self, deps: GraphDeps, **kwargs: Any) -> dict[str, Any]:
        return await self.handler(deps, **kwargs)


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #
async def search_kb(
    deps: GraphDeps,
    *,
    query: str,
    org_id: Any,
    category: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    outcome = await deps.kb.semantic_search(
        query=query, org_id=org_id, namespace=namespace, category=category
    )
    return {
        "context": outcome.context,
        "citations": [c.model_dump() for c in outcome.citations],
        "candidate_count": len(outcome.candidates),
        "max_relevance": outcome.max_relevance_score,
    }


async def semantic_search(
    deps: GraphDeps,
    *,
    query: str,
    org_id: Any,
    category: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    outcome = await deps.kb.semantic_search(
        query=query, org_id=org_id, namespace=namespace, category=category
    )
    return {
        "results": [c.model_dump() for c in outcome.candidates],
        "max_relevance": outcome.max_relevance_score,
    }


async def search_tickets(
    deps: GraphDeps, *, org_id: Any, queue: str, limit: int = 10
) -> dict[str, Any]:
    tickets = await deps.tickets.search(org_id=_as_uuid(org_id), queue=queue, limit=limit)
    return {
        "tickets": [
            {
                "id": str(t.id),
                "subject": t.subject,
                "status": str(t.status),
                "priority": str(t.priority),
            }
            for t in tickets
        ]
    }


async def create_ticket(
    deps: GraphDeps,
    *,
    org_id: Any,
    conversation_id: Any,
    created_by_user_id: Any,
    category: str,
    subject: str,
    escalation_reason: str,
    redacted_transcript: dict[str, Any] | None = None,
    intake_fields: dict[str, Any] | None = None,
    priority: str = TicketPriority.MEDIUM.value,
    final_confidence: float | None = None,
) -> dict[str, Any]:
    ticket = await deps.tickets.create_from_conversation(
        org_id=_as_uuid(org_id),
        conversation_id=_as_uuid(conversation_id),
        created_by_user_id=_as_uuid(created_by_user_id),
        category=category,
        subject=subject,
        escalation_reason=escalation_reason,
        redacted_transcript=redacted_transcript or {},
        intake_fields=intake_fields or {},
        priority=TicketPriority(priority),
        final_confidence=final_confidence,
    )
    return {"ticket_id": str(ticket.id), "queue": ticket.assigned_queue}


async def get_user(deps: GraphDeps, *, user_id: Any) -> dict[str, Any]:
    if deps.users is None:
        return {"user": None}
    user = await deps.users.get_with_role(_as_uuid(user_id))
    if user is None:
        return {"user": None}
    return {
        "user": {
            "id": str(user.id),
            "email": str(user.email),
            "role": user.role.key,
            "org_id": str(user.org_id),
        }
    }


async def get_conversation(
    deps: GraphDeps, *, conversation_id: Any, limit: int = 50
) -> dict[str, Any]:
    if deps.conversations is None:
        return {"messages": []}
    messages = await deps.conversations.list_messages(_as_uuid(conversation_id), limit=limit)
    return {
        "messages": [
            {"turn_id": m.turn_id, "role": str(m.role), "content": m.content} for m in messages
        ]
    }


async def save_memory(
    deps: GraphDeps,
    *,
    org_id: Any,
    user_id: Any,
    key: str,
    value: str,
    conversation_id: Any | None = None,
) -> dict[str, Any]:
    await deps.memory.save_fact(
        org_id=_as_uuid(org_id),
        user_id=_as_uuid(user_id),
        fact_key=key,
        fact_value=value,
        conversation_id=_as_uuid(conversation_id) if conversation_id else None,
    )
    return {"saved": key}


async def notify_engineer(
    deps: GraphDeps,
    *,
    org_id: Any,
    ticket_id: Any,
    queue: str,
    recipient_user_id: Any | None = None,
) -> dict[str, Any]:
    notification = await deps.notifications.notify_engineer(
        org_id=_as_uuid(org_id),
        ticket_id=_as_uuid(ticket_id) if ticket_id else None,
        queue=queue,
        recipient_user_id=_as_uuid(recipient_user_id) if recipient_user_id else None,
        notification_type=NotificationType.HANDOFF,
    )
    return {"notification_id": str(notification.id)}


async def audit_event(
    deps: GraphDeps,
    *,
    org_id: Any,
    action: str,
    resource_type: str,
    actor_user_id: Any | None = None,
    resource_id: Any | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.core.constants import ActorType

    entry = await deps.audit.record(
        org_id=_as_uuid(org_id),
        action=action,
        resource_type=resource_type,
        actor_type=ActorType.AGENT,
        actor_user_id=_as_uuid(actor_user_id) if actor_user_id else None,
        resource_id=_as_uuid(resource_id) if resource_id else None,
        after=properties,
    )
    return {"audit_id": str(entry.id)}


BUILTIN_TOOLS: dict[str, Tool] = {
    "search_kb": Tool(
        "search_kb",
        "Search the knowledge base and return grounded context.",
        search_kb,
        ("query", "org_id", "category"),
    ),
    "semantic_search": Tool(
        "semantic_search",
        "Semantic vector+keyword search over KB chunks.",
        semantic_search,
        ("query", "org_id", "category"),
    ),
    "search_tickets": Tool(
        "search_tickets", "List tickets in a support queue.", search_tickets, ("org_id", "queue")
    ),
    "create_ticket": Tool(
        "create_ticket",
        "Create an engineer-ready support ticket.",
        create_ticket,
        ("org_id", "conversation_id", "category", "subject", "escalation_reason"),
    ),
    "get_user": Tool("get_user", "Fetch a user profile by id.", get_user, ("user_id",)),
    "get_conversation": Tool(
        "get_conversation",
        "Fetch a conversation transcript.",
        get_conversation,
        ("conversation_id",),
    ),
    "save_memory": Tool(
        "save_memory",
        "Persist a durable user fact.",
        save_memory,
        ("org_id", "user_id", "key", "value"),
    ),
    "notify_engineer": Tool(
        "notify_engineer",
        "Notify a support engineer / queue about a handoff.",
        notify_engineer,
        ("org_id", "ticket_id", "queue"),
    ),
    "audit_event": Tool(
        "audit_event",
        "Write an append-only audit-log entry.",
        audit_event,
        ("org_id", "action", "resource_type"),
    ),
}


__all__ = ["Tool", "ToolHandler", "BUILTIN_TOOLS"]
