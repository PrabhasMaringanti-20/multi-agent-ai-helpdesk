"""Health/readiness probe tests (readiness checks are monkeypatched)."""

from __future__ import annotations

import pytest
from app.main import app
from fastapi.testclient import TestClient


async def _healthy() -> bool:
    return True


async def _unhealthy() -> bool:
    return False


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def _all_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.health.check_database", _healthy)
    monkeypatch.setattr("app.api.health.check_redis", _healthy)
    monkeypatch.setattr("app.api.health.check_chroma", _healthy)
    monkeypatch.setattr("app.api.health.check_celery", lambda: True)
    monkeypatch.setattr("app.api.health.providers_configured", lambda: True)


def test_readiness_all_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _all_healthy(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "database": True,
        "redis": True,
        "chroma": True,
        "celery": True,
        "providers": True,
    }


def test_readiness_reports_503_when_dependency_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _all_healthy(monkeypatch)
    monkeypatch.setattr("app.api.health.check_chroma", _unhealthy)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["chroma"] is False
