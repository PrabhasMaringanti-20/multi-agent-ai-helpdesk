"""Unit tests for DI auth dependencies and RBAC guards (no DB required)."""

from __future__ import annotations

import uuid

import pytest
from app.api.deps import get_current_user, require_permissions, require_roles
from app.core.constants import RoleKey
from app.core.exceptions import AuthenticationError, ForbiddenError
from app.core.rbac import Permission
from app.schemas.auth import Principal
from fastapi.security import HTTPAuthorizationCredentials


def _principal(role: str, permissions: list[str]) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        email="user@example.com",
        role=role,
        permissions=frozenset(permissions),
    )


def test_require_roles_allows_matching_role() -> None:
    guard = require_roles(RoleKey.ADMIN.value)
    principal = _principal("admin", [])
    assert guard(principal=principal) is principal


def test_require_roles_denies_other_role() -> None:
    guard = require_roles(RoleKey.ADMIN.value)
    with pytest.raises(ForbiddenError):
        guard(principal=_principal("end_user", []))


def test_require_permissions_allows_when_granted() -> None:
    guard = require_permissions(Permission.KB_PUBLISH)
    principal = _principal("sme_reviewer", [Permission.KB_PUBLISH])
    assert guard(principal=principal) is principal


def test_require_permissions_denies_when_missing() -> None:
    guard = require_permissions(Permission.KB_PUBLISH)
    with pytest.raises(ForbiddenError):
        guard(principal=_principal("end_user", []))


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_credentials() -> None:
    with pytest.raises(AuthenticationError):
        await get_current_user(session=None, credentials=None)


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalid_token() -> None:
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.jwt")
    with pytest.raises(AuthenticationError):
        await get_current_user(session=None, credentials=credentials)
