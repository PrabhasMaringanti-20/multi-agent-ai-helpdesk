"""Generic async repository with tenant-scoped CRUD + query helpers.

Repositories are the only layer that touches the ORM/session. They contain no
business rules (that is the service layer's job) -- only data access. Every
tenant-scoped helper takes an explicit ``org_id`` so the repository can never
accidentally leak across tenants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Async CRUD base. Subclasses set the ``model`` class attribute."""

    model: type[ModelType]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- reads ---------------------------------------------------------- #
    async def get(self, entity_id: Any) -> ModelType | None:
        return await self.session.get(self.model, entity_id)

    async def get_by(self, **filters: Any) -> ModelType | None:
        stmt = select(self.model).filter_by(**filters).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists(self, **filters: Any) -> bool:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
        **filters: Any,
    ) -> Sequence[ModelType]:
        stmt = select(self.model).filter_by(**filters)
        stmt = stmt.order_by(self._default_order() if order_by is None else order_by)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ---- writes --------------------------------------------------------- #
    async def create(self, **values: Any) -> ModelType:
        instance = self.model(**values)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def add(self, instance: ModelType) -> ModelType:
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def update(self, instance: ModelType, **values: Any) -> ModelType:
        for key, value in values.items():
            setattr(instance, key, value)
        await self.session.flush()
        return instance

    async def delete(self, instance: ModelType) -> None:
        await self.session.delete(instance)
        await self.session.flush()

    async def soft_delete(self, instance: ModelType) -> None:
        if not hasattr(instance, "deleted_at"):
            raise AttributeError(
                f"{self.model.__name__} does not support soft delete (no deleted_at)."
            )
        instance.deleted_at = datetime.now(UTC)  # type: ignore[attr-defined]
        await self.session.flush()

    # ---- tenant-scoped helpers ----------------------------------------- #
    async def get_for_org(self, entity_id: Any, org_id: uuid.UUID) -> ModelType | None:
        self._require_tenant()
        stmt = (
            select(self.model)
            .where(self.model.id == entity_id)  # type: ignore[attr-defined]
            .where(self.model.org_id == org_id)  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_org(
        self,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
        **filters: Any,
    ) -> Sequence[ModelType]:
        self._require_tenant()
        stmt = (
            select(self.model)
            .where(self.model.org_id == org_id)  # type: ignore[attr-defined]
            .filter_by(**filters)
        )
        stmt = stmt.order_by(self._default_order() if order_by is None else order_by)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_for_org(self, org_id: uuid.UUID, **filters: Any) -> int:
        self._require_tenant()
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.org_id == org_id)  # type: ignore[attr-defined]
            .filter_by(**filters)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ---- internals ------------------------------------------------------ #
    def _require_tenant(self) -> None:
        if not hasattr(self.model, "org_id"):
            raise AttributeError(f"{self.model.__name__} is not tenant-scoped (no org_id column).")

    def _default_order(self) -> Any:
        for column_name in ("created_at", "occurred_at", "updated_at", "id"):
            column = getattr(self.model, column_name, None)
            if column is not None:
                return column.desc()
        return None
