"""Reusable validation helpers that raise domain ``ValidationError``.

Used by services/routers to validate inputs consistently so failures surface as
RFC 7807 ``422`` problems rather than raw ``ValueError``s.
"""

from __future__ import annotations

import uuid
from typing import TypeVar

from app.core.exceptions import ValidationError

_Number = TypeVar("_Number", int, float)


def parse_uuid(value: object, *, field: str = "id") -> uuid.UUID:
    """Parse ``value`` into a UUID or raise ``ValidationError``."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"'{field}' must be a valid UUID.") from exc


def require(condition: object, message: str) -> None:
    """Raise ``ValidationError(message)`` when ``condition`` is falsy."""
    if not condition:
        raise ValidationError(message)


def clamp(value: _Number, low: _Number, high: _Number) -> _Number:
    """Constrain ``value`` to the inclusive ``[low, high]`` range."""
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, high))


def normalize_str(value: str | None) -> str | None:
    """Trim whitespace; collapse empty strings to ``None``."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


__all__ = ["parse_uuid", "require", "clamp", "normalize_str"]
