"""Feedback request DTO."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.constants import FeedbackRating


class FeedbackRequest(BaseModel):
    conversation_id: uuid.UUID
    rating: FeedbackRating
    message_id: uuid.UUID | None = None
    ticket_id: uuid.UUID | None = None
    comment: str | None = Field(default=None, max_length=2000)
    feedback_handle: str | None = None


__all__ = ["FeedbackRequest"]
