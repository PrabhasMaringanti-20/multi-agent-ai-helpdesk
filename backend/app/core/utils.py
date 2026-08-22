"""Small, dependency-free utility helpers shared across layers."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import TypeVar

_T = TypeVar("_T")


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp."""
    return datetime.now(UTC)


def chunked(sequence: Sequence[_T], size: int) -> Iterator[list[_T]]:
    """Yield successive ``size``-length chunks of ``sequence``."""
    if size <= 0:
        raise ValueError("size must be a positive integer")
    for start in range(0, len(sequence), size):
        yield list(sequence[start : start + size])


def coalesce(*values: _T | None) -> _T | None:
    """Return the first non-``None`` value, or ``None``."""
    for value in values:
        if value is not None:
            return value
    return None


__all__ = ["utcnow", "chunked", "coalesce"]
