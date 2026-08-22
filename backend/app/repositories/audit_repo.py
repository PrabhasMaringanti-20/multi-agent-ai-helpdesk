"""Data access for the append-only audit log.

The audit log is write-once: this repository intentionally exposes only insert
and read operations (no update/delete), mirroring the DB-level INSERT/SELECT
grant policy from ARCHITECTURE.md §10.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.core.constants import ActorType
from app.models.ops import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    model = AuditLog

    async def record(
        self,
        *,
        org_id: uuid.UUID,
        action: str,
        resource_type: str,
        actor_type: ActorType,
        actor_user_id: uuid.UUID | None = None,
        resource_id: uuid.UUID | None = None,
        trace_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            org_id=org_id,
            action=action,
            resource_type=resource_type,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            resource_id=resource_id,
            trace_id=trace_id,
            before=before,
            after=after,
            ip_address=ip_address,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_org(  # type: ignore[override]
        self,
        org_id: uuid.UUID,
        *,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
        **_: Any,
    ) -> Sequence[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.org_id == org_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if resource_type is not None:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if resource_id is not None:
            stmt = stmt.where(AuditLog.resource_id == resource_id)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_by_trace(self, trace_id: str) -> Sequence[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.trace_id == trace_id)
            .order_by(AuditLog.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


__all__ = ["AuditRepository"]
