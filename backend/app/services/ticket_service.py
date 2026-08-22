"""Ticket service — engineer-ready ticket creation + escalation (Phase 9)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from app.core.constants import TicketEventType, TicketPriority, TicketStatus
from app.models.ticket import Ticket
from app.registries.category_registry import CategoryRegistry, get_category_registry
from app.repositories.ticket_repo import TicketRepository


class TicketService:
    def __init__(
        self,
        tickets: TicketRepository,
        categories: CategoryRegistry | None = None,
    ) -> None:
        self._tickets = tickets
        self._categories = categories or get_category_registry()

    async def create_from_conversation(
        self,
        *,
        org_id: uuid.UUID,
        conversation_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        category: str,
        subject: str,
        escalation_reason: str,
        redacted_transcript: dict[str, Any],
        intake_fields: dict[str, Any] | None = None,
        priority: TicketPriority = TicketPriority.MEDIUM,
        final_confidence: float | None = None,
        engineer_hints: dict[str, Any] | None = None,
    ) -> Ticket:
        """Idempotent-per-thread ticket creation with queue routing + event log."""
        existing = await self._tickets.get_by_conversation(conversation_id)
        if existing is not None:
            return existing

        queue = self._categories.get(category).handoff_queue
        ticket = await self._tickets.create(
            org_id=org_id,
            conversation_id=conversation_id,
            created_by_user_id=created_by_user_id,
            category=category,
            priority=priority,
            status=TicketStatus.OPEN,
            assigned_queue=queue,
            subject=subject,
            intake_fields=intake_fields or {},
            escalation_reason=escalation_reason,
            final_confidence=final_confidence,
            engineer_hints=engineer_hints,
            redacted_transcript=redacted_transcript,
        )
        await self._tickets.add_event(
            ticket_id=ticket.id,
            event_type=TicketEventType.CREATED,
            payload={"escalation_reason": escalation_reason, "queue": queue},
        )
        return ticket

    async def search(
        self,
        *,
        org_id: uuid.UUID,
        queue: str,
        statuses: Sequence[TicketStatus] | None = None,
        limit: int = 20,
    ) -> Sequence[Ticket]:
        return await self._tickets.list_queue(org_id, queue, statuses=statuses, limit=limit)


__all__ = ["TicketService"]
