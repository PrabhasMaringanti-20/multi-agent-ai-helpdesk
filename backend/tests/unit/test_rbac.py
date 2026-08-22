"""Unit tests for the RBAC permission matrix (ARCHITECTURE.md §10.3)."""

from __future__ import annotations

from app.core.rbac import (
    ALL_PERMISSIONS,
    Permission,
    permissions_for_role,
    role_has_permission,
)


def test_admin_holds_all_permissions() -> None:
    assert permissions_for_role("admin") == ALL_PERMISSIONS


def test_end_user_baseline() -> None:
    assert role_has_permission("end_user", Permission.CHAT_USE)
    assert role_has_permission("end_user", Permission.FEEDBACK_SUBMIT)
    assert not role_has_permission("end_user", Permission.KB_PUBLISH)
    assert not role_has_permission("end_user", Permission.TICKET_ASSIGN)


def test_support_engineer_can_manage_tickets() -> None:
    assert role_has_permission("support_engineer", Permission.TICKET_ASSIGN)
    assert role_has_permission("support_engineer", Permission.TICKET_RESOLVE)
    assert not role_has_permission("support_engineer", Permission.KB_PUBLISH)


def test_sme_reviewer_can_publish_kb() -> None:
    assert role_has_permission("sme_reviewer", Permission.KB_REVIEW)
    assert role_has_permission("sme_reviewer", Permission.KB_PUBLISH)


def test_audit_read_is_admin_only() -> None:
    assert role_has_permission("admin", Permission.AUDIT_READ)
    for role in ("end_user", "support_engineer", "sme_reviewer"):
        assert not role_has_permission(role, Permission.AUDIT_READ)


def test_unknown_role_has_no_permissions() -> None:
    assert permissions_for_role("does_not_exist") == frozenset()
