"""Shared response DTOs: messages, RFC 7807 problems, and pagination envelopes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class MessageResponse(BaseModel):
    """Simple acknowledgement payload for side-effecting endpoints."""

    detail: str = Field(..., description="Human-readable result message.")


class ProblemDetail(BaseModel):
    """RFC 7807 application/problem+json body (documented response shape)."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(default="about:blank")
    title: str
    status: int
    detail: str | None = None
    trace_id: str | None = None
    errors: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class PaginationParams:
    """Normalized pagination inputs (1-based page + page size)."""

    page: int = 1
    size: int = 20

    @property
    def limit(self) -> int:
        return self.size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class PageMeta(BaseModel):
    """Pagination metadata returned alongside a page of items."""

    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    size: int = Field(..., ge=1)
    pages: int = Field(..., ge=0)


class Page(BaseModel, Generic[T]):
    """A single page of ``items`` plus pagination metadata."""

    items: list[T]
    meta: PageMeta


def build_page(items: Sequence[T], total: int, params: PaginationParams) -> Page[T]:
    """Assemble a :class:`Page` from a slice of ``items`` and the total count."""
    pages = ceil(total / params.size) if params.size else 0
    return Page[T](
        items=list(items),
        meta=PageMeta(total=total, page=params.page, size=params.size, pages=pages),
    )


__all__ = [
    "MessageResponse",
    "ProblemDetail",
    "PaginationParams",
    "PageMeta",
    "Page",
    "build_page",
]
