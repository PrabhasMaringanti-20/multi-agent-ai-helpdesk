"""Unit tests for rate-limiter identity/skip logic (no Redis required)."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.middleware import RateLimitMiddleware


async def _dummy_asgi(scope, receive, send):  # pragma: no cover - not invoked
    return None


def _mw() -> RateLimitMiddleware:
    return RateLimitMiddleware(_dummy_asgi)


def test_skip_probe_and_doc_paths() -> None:
    mw = _mw()
    assert mw._skip("/health")
    assert mw._skip("/health/ready")
    assert mw._skip("/docs")
    assert mw._skip("/openapi.json")
    assert not mw._skip("/api/v1/auth/login")


def test_identity_prefers_authenticated_user() -> None:
    mw = _mw()
    request = SimpleNamespace(
        state=SimpleNamespace(auth={"user_id": "u-1", "role": "admin"}),
        client=SimpleNamespace(host="10.0.0.1"),
    )
    assert mw._identity(request) == "user:u-1"


def test_identity_falls_back_to_ip() -> None:
    mw = _mw()
    request = SimpleNamespace(
        state=SimpleNamespace(auth=None),
        client=SimpleNamespace(host="10.0.0.9"),
    )
    assert mw._identity(request) == "ip:10.0.0.9"
