"""Unit tests for validation helpers and utility functions."""

from __future__ import annotations

import uuid
from datetime import UTC

import pytest
from app.core.exceptions import ValidationError
from app.core.utils import chunked, coalesce, utcnow
from app.core.validation import clamp, normalize_str, parse_uuid, require


def test_parse_uuid_accepts_str_and_uuid() -> None:
    value = uuid.uuid4()
    assert parse_uuid(str(value)) == value
    assert parse_uuid(value) == value


def test_parse_uuid_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        parse_uuid("not-a-uuid", field="ticket_id")


def test_require_raises_on_falsy() -> None:
    require(True, "ok")
    with pytest.raises(ValidationError):
        require(0, "must be truthy")


def test_clamp() -> None:
    assert clamp(5, 0, 10) == 5
    assert clamp(-3, 0, 10) == 0
    assert clamp(42, 0, 10) == 10


def test_normalize_str() -> None:
    assert normalize_str("  hello  ") == "hello"
    assert normalize_str("   ") is None
    assert normalize_str(None) is None


def test_chunked() -> None:
    assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_chunked_rejects_nonpositive_size() -> None:
    with pytest.raises(ValueError):
        list(chunked([1, 2], 0))


def test_coalesce() -> None:
    assert coalesce(None, None, 7) == 7
    assert coalesce(None) is None


def test_utcnow_is_timezone_aware() -> None:
    assert utcnow().tzinfo == UTC
