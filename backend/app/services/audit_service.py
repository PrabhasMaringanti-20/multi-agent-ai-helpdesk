"""Audit service: durable append-only audit-log writes (ARCHITECTURE.md §10).

Audit entries are written at the service layer where the semantic action and its
before/after state are known (e.g. ``auth.login``, ``kb.publish``,
``ticket.resolve``). The current request ``trace_id`` is attached automatically
for end-to-end correlation with logs and ``agent_runs``.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.constants import ActorType
from app.core.logging import get_trace_id
from app.models.ops import AuditLog
from app.repositories.audit_repo import AuditRepository
from app.schemas.auth import Principal


class AuditService:
    def __init__(self, audit: AuditRepository) -> None:
        self.audit = audit

    async def record(
        self,
        *,
        org_id: uuid.UUID,
        action: str,
        resource_type: str,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_user_id: uuid.UUID | None = None,
        resource_id: uuid.UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        return await self.audit.record(
            org_id=org_id,
            action=action,
            resource_type=resource_type,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            resource_id=resource_id,
            trace_id=get_trace_id(),
            before=before,
            after=after,
            ip_address=ip_address,
        )

    async def record_for_principal(
        self,
        principal: Principal,
        *,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        """Convenience wrapper that fills actor fields from the request principal."""
        return await self.record(
            org_id=principal.org_id,
            action=action,
            resource_type=resource_type,
            actor_type=ActorType.USER,
            actor_user_id=principal.user_id,
            resource_id=resource_id,
            before=before,
            after=after,
            ip_address=ip_address,
        )


__all__ = ["AuditService"]
