"""Feedback service — records user feedback and lists unprocessed items."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.constants import FeedbackRating
from app.core.security import generate_jti
from app.models.feedback import Feedback
from app.repositories.feedback_repo import FeedbackRepository


class FeedbackService:
    def __init__(self, feedback: FeedbackRepository) -> None:
        self._feedback = feedback

    async def submit(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        rating: FeedbackRating,
        message_id: uuid.UUID | None = None,
        ticket_id: uuid.UUID | None = None,
        comment: str | None = None,
        feedback_handle: str | None = None,
    ) -> Feedback:
        return await self._feedback.create(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            rating=rating,
            message_id=message_id,
            ticket_id=ticket_id,
            comment=comment,
            feedback_handle=feedback_handle or generate_jti(),
        )

    async def list_unprocessed(self, *, limit: int = 100) -> Sequence[Feedback]:
        return await self._feedback.list_unprocessed(limit=limit)


__all__ = ["FeedbackService"]
