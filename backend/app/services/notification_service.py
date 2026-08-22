"""Notification service — gated, send-only dispatch (persists notifications)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.constants import NotificationChannel, NotificationStatus, NotificationType
from app.models.ops import Notification
from app.repositories.notification_repo import NotificationRepository


class NotificationService:
    def __init__(self, notifications: NotificationRepository) -> None:
        self._notifications = notifications

    async def notify_engineer(
        self,
        *,
        org_id: uuid.UUID,
        ticket_id: uuid.UUID | None,
        queue: str,
        recipient_user_id: uuid.UUID | None = None,
        notification_type: NotificationType = NotificationType.HANDOFF,
        payload: dict[str, Any] | None = None,
    ) -> Notification:
        """Queue a handoff notification. Actual delivery is a gated worker step."""
        channel = NotificationChannel.IN_APP if recipient_user_id else NotificationChannel.QUEUE
        body = {"queue": queue, **(payload or {})}
        return await self._notifications.create(
            org_id=org_id,
            recipient_user_id=recipient_user_id,
            channel=channel,
            type=notification_type,
            payload=body,
            status=NotificationStatus.PENDING,
            ticket_id=ticket_id,
        )


__all__ = ["NotificationService"]
