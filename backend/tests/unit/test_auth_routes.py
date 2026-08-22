"""App-level tests: RBAC guards over ASGI + auth surface presence (no DB).

These verify dependency injection end-to-end (through FastAPI) and the RFC7807
error mapping, using ``dependency_overrides`` to supply a fake principal so no
database is required.
"""

from __future__ import annotations

import uuid

from app.api.deps import get_current_principal, require_permissions, require_roles
from app.api.errors import register_exception_handlers
from app.core.constants import RoleKey
from app.core.rbac import Permission
from app.schemas.auth import Principal
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _principal(role: str, permissions: list[str]) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        email="user@example.com",
        role=role,
        permissions=frozenset(permissions),
    )


def _build_app(principal: Principal) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/admin-only", dependencies=[Depends(require_roles(RoleKey.ADMIN.value))])
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}

    @app.get(
        "/can-publish",
        dependencies=[Depends(require_permissions(Permission.KB_PUBLISH))],
    )
    async def can_publish() -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_current_principal] = lambda: principal
    return app


def test_admin_role_allowed() -> None:
    client = TestClient(_build_app(_principal("admin", [])))
    assert client.get("/admin-only").status_code == 200


def test_non_admin_role_forbidden() -> None:
    client = TestClient(_build_app(_principal("end_user", [])))
    response = client.get("/admin-only")
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "forbidden"


def test_permission_granted() -> None:
    client = TestClient(_build_app(_principal("sme_reviewer", [Permission.KB_PUBLISH])))
    assert client.get("/can-publish").status_code == 200


def test_permission_denied() -> None:
    client = TestClient(_build_app(_principal("end_user", [])))
    assert client.get("/can-publish").status_code == 403


def test_main_app_exposes_auth_surface() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/health" in paths
    assert any(p.endswith("/auth/login") for p in paths)
    assert any(p.endswith("/auth/register") for p in paths)
    assert any(p.endswith("/auth/refresh") for p in paths)
    assert any(p.endswith("/auth/logout") for p in paths)
    assert any(p.endswith("/auth/me") for p in paths)


def test_health_endpoint_serves() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
