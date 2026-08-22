"""Feedback route."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentPrincipal, SessionDep
from app.repositories.feedback_repo import FeedbackRepository
from app.schemas.common import MessageResponse
from app.schemas.feedback import FeedbackRequest
from app.services.feedback_service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", status_code=status.HTTP_201_CREATED, summary="Submit answer feedback")
async def submit_feedback(
    payload: FeedbackRequest, principal: CurrentPrincipal, session: SessionDep
) -> MessageResponse:
    service = FeedbackService(FeedbackRepository(session))
    await service.submit(
        org_id=principal.org_id,
        user_id=principal.user_id,
        conversation_id=payload.conversation_id,
        rating=payload.rating,
        message_id=payload.message_id,
        ticket_id=payload.ticket_id,
        comment=payload.comment,
        feedback_handle=payload.feedback_handle,
    )
    return MessageResponse(detail="Thanks for your feedback.")


__all__ = ["router"]
