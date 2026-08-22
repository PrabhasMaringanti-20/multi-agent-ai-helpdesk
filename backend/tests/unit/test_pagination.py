"""Unit tests for pagination models/helpers."""

from __future__ import annotations

from app.schemas.common import PaginationParams, build_page


def test_offset_and_limit() -> None:
    params = PaginationParams(page=3, size=20)
    assert params.limit == 20
    assert params.offset == 40


def test_first_page_offset_is_zero() -> None:
    assert PaginationParams(page=1, size=50).offset == 0


def test_build_page_computes_meta() -> None:
    page = build_page(items=[1, 2, 3], total=53, params=PaginationParams(page=1, size=20))
    assert page.items == [1, 2, 3]
    assert page.meta.total == 53
    assert page.meta.page == 1
    assert page.meta.size == 20
    assert page.meta.pages == 3  # ceil(53/20)


def test_build_page_zero_total() -> None:
    page = build_page(items=[], total=0, params=PaginationParams(page=1, size=20))
    assert page.meta.pages == 0
    assert page.items == []
