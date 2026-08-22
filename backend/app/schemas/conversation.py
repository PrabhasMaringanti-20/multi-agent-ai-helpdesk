"""Conversation + message response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None = None
    status: str
    category: str | None = None
    last_message_at: datetime | None = None


class MessageDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    turn_id: int
    role: str
    content: str
    citations: list[dict] | None = None


__all__ = ["ConversationResponse", "MessageDTO"]
