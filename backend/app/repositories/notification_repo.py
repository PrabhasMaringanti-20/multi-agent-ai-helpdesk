"""Data access for notifications."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.constants import NotificationStatus
from app.models.ops import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    model = Notification

    async def list_for_user(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Notification]:
        stmt = select(Notification).where(
            Notification.org_id == org_id,
            Notification.recipient_user_id == user_id,
        )
        if unread_only:
            stmt = stmt.where(Notification.status != NotificationStatus.READ)
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_unread(self, org_id: uuid.UUID, user_id: uuid.UUID) -> int:
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.org_id == org_id,
                Notification.recipient_user_id == user_id,
                Notification.status != NotificationStatus.READ,
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def mark_read(self, notification: Notification) -> Notification:
        notification.status = NotificationStatus.READ
        notification.read_at = datetime.now(UTC)
        await self.session.flush()
        return notification

    async def mark_sent(self, notification: Notification) -> Notification:
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(UTC)
        await self.session.flush()
        return notification


__all__ = ["NotificationRepository"]
