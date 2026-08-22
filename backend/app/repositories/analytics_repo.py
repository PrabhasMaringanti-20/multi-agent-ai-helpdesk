"""Data access for the analytics event stream and precomputed usage rollups."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.models.ops import AnalyticsEvent, UsageStatistic
from app.repositories.base import BaseRepository


class AnalyticsRepository(BaseRepository[AnalyticsEvent]):
    model = AnalyticsEvent

    async def record_event(
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
        event = AnalyticsEvent(
            org_id=org_id,
            event_type=event_type,
            user_id=user_id,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            category=category,
            properties=properties or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def count_by_type(
        self,
        org_id: uuid.UUID,
        event_type: str,
        *,
        since: datetime | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(
                AnalyticsEvent.org_id == org_id,
                AnalyticsEvent.event_type == event_type,
            )
        )
        if since is not None:
            stmt = stmt.where(AnalyticsEvent.occurred_at >= since)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def counts_grouped_by_type(
        self,
        org_id: uuid.UUID,
        *,
        since: datetime | None = None,
    ) -> dict[str, int]:
        stmt = (
            select(AnalyticsEvent.event_type, func.count())
            .where(AnalyticsEvent.org_id == org_id)
            .group_by(AnalyticsEvent.event_type)
        )
        if since is not None:
            stmt = stmt.where(AnalyticsEvent.occurred_at >= since)
        result = await self.session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    async def list_rollups(
        self,
        org_id: uuid.UUID,
        *,
        metric_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[UsageStatistic]:
        stmt = select(UsageStatistic).where(UsageStatistic.org_id == org_id)
        if metric_key is not None:
            stmt = stmt.where(UsageStatistic.metric_key == metric_key)
        stmt = stmt.order_by(UsageStatistic.period_start.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()


__all__ = ["AnalyticsRepository"]
