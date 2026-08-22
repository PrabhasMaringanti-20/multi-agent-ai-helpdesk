"""Knowledge-base response DTO."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    doc_status: str
    version: int
    last_verified_at: datetime | None = None


__all__ = ["KnowledgeDocumentResponse"]
