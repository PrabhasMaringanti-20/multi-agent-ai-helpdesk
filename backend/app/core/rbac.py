"""Role-based access control matrix and permission catalog (ARCHITECTURE.md §10.3).

Defines the canonical permission strings and the role -> permission grants for
the four seed roles (``end_user``, ``support_engineer``, ``sme_reviewer``,
``admin``). This in-code matrix is the authoritative definition used to derive
the ``roles.permissions`` JWT fast-path cache and to answer permission checks in
``api.deps.require_permissions``.

Notable policy from §10.3: audit-log read is ``admin`` only; ``kb:publish`` is
granted to ``admin`` and ``sme_reviewer``.
"""

from __future__ import annotations

from app.core.constants import RoleKey


class Permission:
    """Namespace of canonical permission strings (``resource:action``)."""

    CHAT_USE = "chat:use"
    CONVERSATION_READ = "conversation:read"
    CONVERSATION_WRITE = "conversation:write"

    TICKET_READ = "ticket:read"
    TICKET_WRITE = "ticket:write"
    TICKET_ASSIGN = "ticket:assign"
    TICKET_RESOLVE = "ticket:resolve"

    KB_READ = "kb:read"
    KB_WRITE = "kb:write"
    KB_REVIEW = "kb:review"
    KB_PUBLISH = "kb:publish"

    FEEDBACK_SUBMIT = "feedback:submit"
    NOTIFICATION_READ = "notification:read"
    FILE_UPLOAD = "file:upload"
    FILE_READ = "file:read"
    ANALYTICS_READ = "analytics:read"
    AUDIT_READ = "audit:read"

    ADMIN_MANAGE_USERS = "admin:manage_users"
    ADMIN_MANAGE_CATEGORIES = "admin:manage_categories"
    ADMIN_MANAGE_SETTINGS = "admin:manage_settings"


# Full catalog (every defined permission).
ALL_PERMISSIONS: frozenset[str] = frozenset(
    value
    for name, value in vars(Permission).items()
    if not name.startswith("_") and isinstance(value, str)
)

_END_USER_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.CHAT_USE,
        Permission.CONVERSATION_READ,
        Permission.CONVERSATION_WRITE,
        Permission.TICKET_READ,
        Permission.KB_READ,
        Permission.FEEDBACK_SUBMIT,
        Permission.NOTIFICATION_READ,
        Permission.FILE_UPLOAD,
    }
)

_SUPPORT_ENGINEER_PERMISSIONS: frozenset[str] = _END_USER_PERMISSIONS | {
    Permission.TICKET_WRITE,
    Permission.TICKET_ASSIGN,
    Permission.TICKET_RESOLVE,
    Permission.KB_WRITE,
    Permission.FILE_READ,
    Permission.ANALYTICS_READ,
}

_SME_REVIEWER_PERMISSIONS: frozenset[str] = _SUPPORT_ENGINEER_PERMISSIONS | {
    Permission.KB_REVIEW,
    Permission.KB_PUBLISH,
}

# Admin is a strict superset of every permission (incl. audit:read + admin:*).
_ADMIN_PERMISSIONS: frozenset[str] = ALL_PERMISSIONS


ROLE_PERMISSIONS: dict[RoleKey, frozenset[str]] = {
    RoleKey.END_USER: _END_USER_PERMISSIONS,
    RoleKey.SUPPORT_ENGINEER: _SUPPORT_ENGINEER_PERMISSIONS,
    RoleKey.SME_REVIEWER: _SME_REVIEWER_PERMISSIONS,
    RoleKey.ADMIN: _ADMIN_PERMISSIONS,
}


def permissions_for_role(role_key: str) -> frozenset[str]:
    """Return the permission set granted to ``role_key`` (empty if unknown)."""
    try:
        return ROLE_PERMISSIONS[RoleKey(role_key)]
    except ValueError:
        return frozenset()


def role_has_permission(role_key: str, permission: str) -> bool:
    return permission in permissions_for_role(role_key)


__all__ = [
    "Permission",
    "ALL_PERMISSIONS",
    "ROLE_PERMISSIONS",
    "permissions_for_role",
    "role_has_permission",
]
