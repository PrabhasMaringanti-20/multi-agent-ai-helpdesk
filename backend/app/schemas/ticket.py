"""Ticket response DTO."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str
    category: str
    priority: str
    status: str
    assigned_queue: str
    created_at: datetime | None = None


__all__ = ["TicketResponse"]
