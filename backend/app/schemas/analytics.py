"""Analytics response DTO."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyticsSummary(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)


__all__ = ["AnalyticsSummary"]
