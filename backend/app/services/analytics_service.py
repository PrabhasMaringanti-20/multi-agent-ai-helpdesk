"""Analytics service — records events to the analytics stream."""

from __future__ import annotations

import uuid
from typing import Any

from app.models.ops import AnalyticsEvent
from app.repositories.analytics_repo import AnalyticsRepository


class AnalyticsService:
    def __init__(self, analytics: AnalyticsRepository) -> None:
        self._analytics = analytics

    async def record(
        self,
        *,
        org_id: uuid.UUID,
        event_type: str,
        user_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        ticket_id: uuid.UUID | None = None,
        category: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> AnalyticsEvent:
        return await self._analytics.record_event(
            org_id=org_id,
            event_type=event_type,
            user_id=user_id,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            category=category,
            properties=properties or {},
        )


__all__ = ["AnalyticsService"]
