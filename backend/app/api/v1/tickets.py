"""Ticket routes.

End-users see their own tickets; engineers/admins see the whole org. Also
exposes ticket detail and the user<->engineer message thread (#8), which reuses
``ticket_events`` rows of type COMMENTED so no schema change is required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import (
    CurrentPrincipal,
    PaginationDep,
    SessionDep,
    get_graph_deps,
    require_roles,
)
from app.core.constants import (
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    RoleKey,
    TicketEventType,
)
from app.core.exceptions import NotFoundError
from app.models.ops import Notification
from app.models.ticket import Ticket
from app.providers.base import ChatMessage
from app.registries.category_registry import get_category_registry
from app.repositories.kb_repo import KnowledgeRepository
from app.repositories.ticket_repo import TicketRepository
from app.schemas.common import Page, build_page
from app.schemas.ticket import TicketResponse

router = APIRouter(prefix="/tickets", tags=["tickets"])

_ENGINEER_ROLES = {RoleKey.SUPPORT_ENGINEER.value, RoleKey.SME_REVIEWER.value, RoleKey.ADMIN.value}


class TicketDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str
    category: str
    priority: str
    status: str
    assigned_queue: str
    escalation_reason: str
    final_confidence: float | None = None
    assigned_engineer_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID
    intake_fields: dict = {}
    created_at: datetime | None = None


class TicketMessageDTO(BaseModel):
    id: uuid.UUID
    sender_role: str
    sender_email: str | None = None
    text: str
    created_at: datetime | None = None


class PostMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _can_access(principal: object, ticket: object) -> bool:
    return principal.role in _ENGINEER_ROLES or (ticket.created_by_user_id == principal.user_id)


@router.get("", summary="List tickets")
async def list_tickets(
    principal: CurrentPrincipal, session: SessionDep, pagination: PaginationDep
) -> Page[TicketResponse]:
    repo = TicketRepository(session)
    filters: dict = (
        {} if principal.role in _ENGINEER_ROLES else {"created_by_user_id": principal.user_id}
    )
    rows = await repo.list_for_org(
        principal.org_id, limit=pagination.limit, offset=pagination.offset, **filters
    )
    total = await repo.count_for_org(principal.org_id, **filters)
    return build_page([TicketResponse.model_validate(t) for t in rows], total, pagination)


class TicketStats(BaseModel):
    total: int
    open: int
    resolved: int
    urgent: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    daily: list[dict[str, Any]]  # last 7 days: [{date, count}]


@router.get("/stats", summary="Ticket KPIs + status/priority breakdown + 7-day trend")
async def ticket_stats(principal: CurrentPrincipal, session: SessionDep) -> TicketStats:
    base = [Ticket.org_id == principal.org_id, Ticket.deleted_at.is_(None)]
    if principal.role not in _ENGINEER_ROLES:  # end users only see their own
        base.append(Ticket.created_by_user_id == principal.user_id)

    status_rows = (
        await session.execute(
            select(Ticket.status, func.count()).where(*base).group_by(Ticket.status)
        )
    ).all()
    by_status = {str(s): int(n) for s, n in status_rows}
    prio_rows = (
        await session.execute(
            select(Ticket.priority, func.count()).where(*base).group_by(Ticket.priority)
        )
    ).all()
    by_priority = {str(p): int(n) for p, n in prio_rows}

    total = sum(by_status.values())
    resolved = by_status.get("resolved", 0) + by_status.get("closed", 0)
    urgent = by_priority.get("urgent", 0)

    since = datetime.now(UTC) - timedelta(days=6)
    day_rows = (
        await session.execute(
            select(func.date(Ticket.created_at), func.count())
            .where(*base, Ticket.created_at >= since)
            .group_by(func.date(Ticket.created_at))
        )
    ).all()
    by_day = {str(d): int(n) for d, n in day_rows}
    daily = [
        {
            "date": (datetime.now(UTC) - timedelta(days=6 - i)).date().isoformat(),
            "count": by_day.get((datetime.now(UTC) - timedelta(days=6 - i)).date().isoformat(), 0),
        }
        for i in range(7)
    ]
    return TicketStats(
        total=total,
        open=total - resolved,
        resolved=resolved,
        urgent=urgent,
        by_status=by_status,
        by_priority=by_priority,
        daily=daily,
    )


@router.get("/{ticket_id}", summary="Ticket detail")
async def get_ticket(
    ticket_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> TicketDetailResponse:
    repo = TicketRepository(session)
    ticket = await repo.get_for_org(ticket_id, principal.org_id)
    if ticket is None or not _can_access(principal, ticket):
        raise NotFoundError("Ticket not found.")
    return TicketDetailResponse.model_validate(ticket)


@router.get("/{ticket_id}/messages", summary="User<->engineer message thread")
async def list_messages(
    ticket_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> list[TicketMessageDTO]:
    repo = TicketRepository(session)
    ticket = await repo.get_for_org(ticket_id, principal.org_id)
    if ticket is None or not _can_access(principal, ticket):
        raise NotFoundError("Ticket not found.")
    out: list[TicketMessageDTO] = []
    for ev in await repo.list_events(ticket_id):
        if ev.event_type == TicketEventType.COMMENTED:
            p = ev.payload or {}
            out.append(
                TicketMessageDTO(
                    id=ev.id,
                    sender_role=p.get("sender_role", "user"),
                    sender_email=p.get("sender_email"),
                    text=p.get("text", ""),
                    created_at=ev.created_at,
                )
            )
    return out


@router.post("/{ticket_id}/messages", summary="Post a message to a ticket thread")
async def post_message(
    ticket_id: uuid.UUID,
    payload: PostMessageRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> TicketMessageDTO:
    repo = TicketRepository(session)
    ticket = await repo.get_for_org(ticket_id, principal.org_id)
    if ticket is None or not _can_access(principal, ticket):
        raise NotFoundError("Ticket not found.")
    sender_role = "engineer" if principal.role in _ENGINEER_ROLES else "user"
    ev = await repo.add_event(
        ticket_id=ticket_id,
        event_type=TicketEventType.COMMENTED,
        actor_user_id=principal.user_id,
        payload={"text": payload.text, "sender_role": sender_role, "sender_email": principal.email},
    )
    # Notify the other party (engineer <-> creator) of the new message.
    recipient = ticket.assigned_engineer_id if sender_role == "user" else ticket.created_by_user_id
    if recipient and recipient != principal.user_id:
        session.add(
            Notification(
                org_id=principal.org_id,
                recipient_user_id=recipient,
                channel=NotificationChannel.IN_APP,
                type=NotificationType.MENTION,
                status=NotificationStatus.SENT,
                ticket_id=ticket.id,
                payload={"title": "New message", "body": f"New message on '{ticket.subject}'."},
            )
        )
    await session.commit()
    return TicketMessageDTO(
        id=ev.id,
        sender_role=sender_role,
        sender_email=principal.email,
        text=payload.text,
        created_at=ev.created_at or datetime.now(UTC),
    )


class DraftKbResponse(BaseModel):
    doc_id: uuid.UUID
    title: str
    status: str
    created: bool  # False if a draft for this ticket already existed


@router.post(
    "/{ticket_id}/draft-kb",
    dependencies=[
        Depends(
            require_roles(
                RoleKey.SUPPORT_ENGINEER.value, RoleKey.SME_REVIEWER.value, RoleKey.ADMIN.value
            )
        )
    ],
    summary="Draft a KB article from this resolved ticket (LLM) — files it for SME review",
)
async def draft_kb_from_ticket(
    ticket_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> DraftKbResponse:
    from app.providers.registry import get_llm_provider
    from app.services.kb_draft_service import KbDraftService

    repo = TicketRepository(session)
    ticket = await repo.get_for_org(ticket_id, principal.org_id)
    if ticket is None:
        raise NotFoundError("Ticket not found.")
    service = KbDraftService(session, get_llm_provider("large"), KnowledgeRepository(session), repo)
    doc, created = await service.draft_from_ticket(ticket, principal)
    return DraftKbResponse(
        doc_id=doc.id, title=doc.title, status=str(doc.doc_status), created=created
    )


class SuggestReplyResponse(BaseModel):
    suggestion: str


@router.post(
    "/{ticket_id}/suggest-reply",
    dependencies=[
        Depends(
            require_roles(
                RoleKey.SUPPORT_ENGINEER.value, RoleKey.SME_REVIEWER.value, RoleKey.ADMIN.value
            )
        )
    ],
    summary="AI-drafted engineer reply (Gemini, grounded in the knowledge base)",
)
async def suggest_reply(
    ticket_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> SuggestReplyResponse:
    repo = TicketRepository(session)
    ticket = await repo.get_for_org(ticket_id, principal.org_id)
    if ticket is None:
        raise NotFoundError("Ticket not found.")
    deps = get_graph_deps(session)
    namespace = get_category_registry().get(ticket.category).retrieval_namespace
    try:
        outcome = await deps.retriever.retrieve(
            query=ticket.subject,
            org_id=str(principal.org_id),
            namespace=namespace,
            category=ticket.category,
        )
        context = outcome.context or "(no matching knowledge-base articles)"
    except Exception:  # noqa: BLE001 - degrade to a generic draft
        context = "(no matching knowledge-base articles)"
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You are a senior IT support engineer drafting a short, friendly reply to "
                "the end user on their ticket. Use the SOURCES when relevant; be concrete "
                "and actionable in 3-6 sentences. Write a natural message (no markdown "
                "headings). Do not invent internal details or promise timelines."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Ticket subject: {ticket.subject}\nCategory: {ticket.category}\n\n"
                f"SOURCES:\n{context}\n\nDraft the reply to the user:"
            ),
        ),
    ]
    result = await deps.llm_large.generate(messages)
    return SuggestReplyResponse(suggestion=result.text.strip())


__all__ = ["router"]
