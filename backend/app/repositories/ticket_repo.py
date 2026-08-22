"""Data access for tickets and their event/assignment history."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, update

from app.core.constants import TicketEventType, TicketStatus
from app.models.ticket import Ticket, TicketAssignment, TicketEvent
from app.repositories.base import BaseRepository


class TicketRepository(BaseRepository[Ticket]):
    model = Ticket

    async def get_by_conversation(self, conversation_id: uuid.UUID) -> Ticket | None:
        return await self.get_by(conversation_id=conversation_id)

    async def list_queue(
        self,
        org_id: uuid.UUID,
        assigned_queue: str,
        *,
        statuses: Sequence[TicketStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Ticket]:
        stmt = select(Ticket).where(
            Ticket.org_id == org_id,
            Ticket.assigned_queue == assigned_queue,
            Ticket.deleted_at.is_(None),
        )
        if statuses:
            stmt = stmt.where(Ticket.status.in_(list(statuses)))
        stmt = (
            stmt.order_by(Ticket.priority.desc(), Ticket.sla_due_at.asc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_for_engineer(
        self,
        org_id: uuid.UUID,
        engineer_id: uuid.UUID,
        *,
        statuses: Sequence[TicketStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Ticket]:
        stmt = select(Ticket).where(
            Ticket.org_id == org_id,
            Ticket.assigned_engineer_id == engineer_id,
            Ticket.deleted_at.is_(None),
        )
        if statuses:
            stmt = stmt.where(Ticket.status.in_(list(statuses)))
        stmt = stmt.order_by(Ticket.updated_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def add_event(
        self,
        *,
        ticket_id: uuid.UUID,
        event_type: TicketEventType,
        actor_user_id: uuid.UUID | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TicketEvent:
        event = TicketEvent(
            ticket_id=ticket_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            from_status=from_status,
            to_status=to_status,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events(self, ticket_id: uuid.UUID) -> Sequence[TicketEvent]:
        stmt = (
            select(TicketEvent)
            .where(TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def record_assignment(
        self,
        *,
        ticket_id: uuid.UUID,
        assigned_to_user_id: uuid.UUID,
        assigned_queue: str,
        assignment_reason: Any,
        assigned_by_user_id: uuid.UUID | None = None,
    ) -> TicketAssignment:
        """Retire the prior current assignment and record a new current one."""
        await self.session.execute(
            update(TicketAssignment)
            .where(
                TicketAssignment.ticket_id == ticket_id,
                TicketAssignment.is_current.is_(True),
            )
            .values(is_current=False)
        )
        assignment = TicketAssignment(
            ticket_id=ticket_id,
            assigned_to_user_id=assigned_to_user_id,
            assigned_by_user_id=assigned_by_user_id,
            assigned_queue=assigned_queue,
            assignment_reason=assignment_reason,
            is_current=True,
        )
        self.session.add(assignment)
        await self.session.flush()
        return assignment


__all__ = ["TicketRepository"]
