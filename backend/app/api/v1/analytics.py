"""Analytics routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, SessionDep, require_permissions
from app.core.rbac import Permission
from app.repositories.analytics_repo import AnalyticsRepository
from app.schemas.analytics import AnalyticsSummary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/summary",
    dependencies=[Depends(require_permissions(Permission.ANALYTICS_READ))],
    summary="Event counts grouped by type",
)
async def summary(principal: CurrentPrincipal, session: SessionDep) -> AnalyticsSummary:
    repo = AnalyticsRepository(session)
    counts = await repo.counts_grouped_by_type(principal.org_id)
    return AnalyticsSummary(counts=counts)


__all__ = ["router"]
