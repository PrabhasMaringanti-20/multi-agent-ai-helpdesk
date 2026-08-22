"""Threshold registry — per-category/sensitivity gate thresholds (§1.3 / §8).

Resolves the deliver/retrieval/grounding thresholds and retry budget for a
category, tightening them for high-sensitivity turns (payment/security). Values
come from the category registry's ``thresholds`` jsonb, falling back to safe
defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.constants import SensitivityLevel
from app.registries.category_registry import CategoryRegistry, get_category_registry


@dataclass(frozen=True)
class ThresholdSet:
    retrieval: float
    deliver: float
    grounding_min: float
    retry_budget: int


_DEFAULTS = ThresholdSet(retrieval=0.72, deliver=0.75, grounding_min=0.70, retry_budget=1)


class ThresholdRegistry:
    def __init__(self, categories: CategoryRegistry | None = None) -> None:
        self._categories = categories or get_category_registry()

    def for_category(
        self, category_key: str | None, sensitivity: SensitivityLevel = SensitivityLevel.LOW
    ) -> ThresholdSet:
        raw = self._categories.get(category_key).thresholds
        base = ThresholdSet(
            retrieval=float(raw.get("retrieval", _DEFAULTS.retrieval)),
            deliver=float(raw.get("deliver", _DEFAULTS.deliver)),
            grounding_min=float(raw.get("grounding_min", _DEFAULTS.grounding_min)),
            retry_budget=int(raw.get("retry_budget", _DEFAULTS.retry_budget)),
        )
        if sensitivity == SensitivityLevel.HIGH:
            # Tighten and remove the retry budget for sensitive categories.
            return ThresholdSet(
                retrieval=min(0.99, base.retrieval + 0.08),
                deliver=min(0.99, base.deliver + 0.08),
                grounding_min=min(0.99, base.grounding_min + 0.08),
                retry_budget=max(0, base.retry_budget - 1),
            )
        return base


_registry: ThresholdRegistry | None = None


def get_threshold_registry() -> ThresholdRegistry:
    global _registry
    if _registry is None:
        _registry = ThresholdRegistry()
    return _registry


__all__ = ["ThresholdSet", "ThresholdRegistry", "get_threshold_registry"]
