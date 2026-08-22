"""Notification routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, PaginationDep, SessionDep
from app.core.exceptions import NotFoundError
from app.repositories.notification_repo import NotificationRepository
from app.schemas.common import MessageResponse, Page, build_page
from app.schemas.notification import NotificationResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", summary="List the current user's notifications")
async def list_notifications(
    principal: CurrentPrincipal, session: SessionDep, pagination: PaginationDep
) -> Page[NotificationResponse]:
    repo = NotificationRepository(session)
    rows = await repo.list_for_user(
        principal.org_id, principal.user_id, limit=pagination.limit, offset=pagination.offset
    )
    total = await repo.count_for_org(principal.org_id, recipient_user_id=principal.user_id)
    return build_page([NotificationResponse.model_validate(n) for n in rows], total, pagination)


@router.get("/unread-count", summary="Count of unread notifications (for the bell badge)")
async def unread_count(principal: CurrentPrincipal, session: SessionDep) -> dict[str, int]:
    repo = NotificationRepository(session)
    return {"count": await repo.count_unread(principal.org_id, principal.user_id)}


@router.post("/read-all", summary="Mark all notifications read")
async def mark_all_read(principal: CurrentPrincipal, session: SessionDep) -> MessageResponse:
    repo = NotificationRepository(session)
    rows = await repo.list_for_user(
        principal.org_id, principal.user_id, unread_only=True, limit=200
    )
    for n in rows:
        await repo.mark_read(n)
    await session.commit()
    return MessageResponse(detail=f"Marked {len(rows)} read.")


@router.post("/{notification_id}/read", summary="Mark a notification read")
async def mark_read(
    notification_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> MessageResponse:
    repo = NotificationRepository(session)
    notification = await repo.get_for_org(notification_id, principal.org_id)
    if notification is None:
        raise NotFoundError("Notification not found.")
    await repo.mark_read(notification)
    return MessageResponse(detail="Marked read.")


__all__ = ["router"]
