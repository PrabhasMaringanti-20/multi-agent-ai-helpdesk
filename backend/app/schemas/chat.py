"""Chat request DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatTurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    thread_id: str | None = Field(
        default=None, description="Conversation/thread id; a new one is created if omitted."
    )


__all__ = ["ChatTurnRequest"]
