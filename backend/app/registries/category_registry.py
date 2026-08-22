"""Category registry — the data-driven extensibility seam (§7.5 / §1.3).

Loads the ``category_registry`` rows (namespace, intake slots, SLA, handoff
queue, thresholds, tool bindings). Ships with in-memory defaults mirroring the
8 seed categories (from migration 0003) so the engine is fully functional
before/without a database; ``load_from_db`` overrides them from Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CategoryConfig:
    category_key: str
    display_name: str
    retrieval_namespace: str
    sla_tier: str
    handoff_queue: str
    required_intake_fields: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    tool_bindings: list[str] = field(default_factory=list)
    is_active: bool = True


_DEFAULT_TOOLS = ["search_kb", "semantic_search", "create_ticket", "get_conversation"]

DEFAULT_CATEGORIES: dict[str, CategoryConfig] = {
    "login_issue": CategoryConfig(
        "login_issue",
        "Login Issues",
        "login_issue",
        "standard",
        "identity_access",
        {"username": "string", "error_message": "string"},
        {"retrieval": 0.72, "deliver": 0.75, "grounding_min": 0.70, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
    "password_reset": CategoryConfig(
        "password_reset",
        "Password Reset",
        "password_reset",
        "standard",
        "identity_access",
        {"username": "string", "account_type": "string"},
        {"retrieval": 0.70, "deliver": 0.72, "grounding_min": 0.68, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
    "vpn": CategoryConfig(
        "vpn",
        "VPN Problems",
        "vpn",
        "standard",
        "network_access",
        {"vpn_client": "string", "os": "string", "error_code": "string"},
        {"retrieval": 0.72, "deliver": 0.75, "grounding_min": 0.70, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
    "payment": CategoryConfig(
        "payment",
        "Payment Issues",
        "payment",
        "priority",
        "billing",
        {"invoice_id": "string", "amount": "string", "payment_method": "string"},
        {"retrieval": 0.82, "deliver": 0.88, "grounding_min": 0.82, "retry_budget": 0},
        _DEFAULT_TOOLS,
    ),
    "software_install": CategoryConfig(
        "software_install",
        "Software Installation",
        "software_install",
        "standard",
        "endpoint_support",
        {"software_name": "string", "os": "string", "version": "string"},
        {"retrieval": 0.70, "deliver": 0.75, "grounding_min": 0.70, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
    "application_error": CategoryConfig(
        "application_error",
        "Application Errors",
        "application_error",
        "standard",
        "app_support",
        {"application": "string", "error_message": "string", "steps_to_reproduce": "string"},
        {"retrieval": 0.72, "deliver": 0.76, "grounding_min": 0.72, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
    "email": CategoryConfig(
        "email",
        "Email Problems",
        "email",
        "standard",
        "messaging",
        {"email_client": "string", "error_message": "string"},
        {"retrieval": 0.70, "deliver": 0.74, "grounding_min": 0.70, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
    "hardware_request": CategoryConfig(
        "hardware_request",
        "Hardware Requests",
        "hardware_request",
        "standard",
        "asset_management",
        {"device_type": "string", "justification": "string"},
        {"retrieval": 0.70, "deliver": 0.75, "grounding_min": 0.70, "retry_budget": 1},
        _DEFAULT_TOOLS,
    ),
}

# --------------------------------------------------------------------------- #
# Extended enterprise demo categories (data-driven extensibility seam, §7.5).
# Additive only: `setdefault` never clobbers the 8 canonical categories above.
# Most answer directly (no required slots); a few carry a single ``issue_type``
# slot so they showcase the guided quick-reply clarification flow.
# --------------------------------------------------------------------------- #
_STD_TH = {"retrieval": 0.60, "deliver": 0.66, "grounding_min": 0.60, "retry_budget": 1}
_GUIDED = {"issue_type": "string"}  # triggers a guided clarification with quick replies

_EXTRA_CATEGORIES: list[tuple[str, str, str, dict]] = [
    ("mfa", "MFA Issues", "identity_access", {}),
    ("account_locked", "Account Locked", "identity_access", {}),
    ("active_directory", "Active Directory", "identity_access", {}),
    ("access_request", "Access Requests", "identity_access", {}),
    ("vpn_certificate", "VPN Certificate", "network_access", {}),
    ("wifi", "WiFi Connectivity", "network_access", _GUIDED),
    ("remote_desktop", "Remote Desktop", "network_access", {}),
    ("network_drives", "Network Drives", "network_access", {}),
    ("outlook", "Outlook Issues", "messaging", _GUIDED),
    ("teams", "Teams Issues", "messaging", _GUIDED),
    ("email_config", "Email Configuration", "messaging", {}),
    ("browser", "Browser Problems", "endpoint_support", _GUIDED),
    ("shared_folder", "Shared Folder Access", "identity_access", {}),
    ("printer", "Printer Problems", "endpoint_support", _GUIDED),
    ("laptop_performance", "Laptop Slow / Performance", "endpoint_support", {}),
    ("blue_screen", "Blue Screen (BSOD)", "endpoint_support", {}),
    ("windows_update", "Windows Update", "endpoint_support", {}),
    ("antivirus", "Antivirus", "endpoint_support", {}),
    ("office_activation", "Office Activation", "endpoint_support", {}),
    ("sap", "SAP Access", "app_support", {}),
    ("oracle", "Oracle", "app_support", {}),
    ("database_connection", "Database Connection", "app_support", {}),
    ("git_access", "Git Access", "app_support", {}),
    ("vscode", "VS Code", "app_support", {}),
    ("docker", "Docker", "app_support", {}),
    ("python_env", "Python Environment", "app_support", {}),
    ("java_env", "Java Environment", "app_support", {}),
    ("node_env", "Node.js Environment", "app_support", {}),
]
for _k, _n, _q, _slots in _EXTRA_CATEGORIES:
    DEFAULT_CATEGORIES.setdefault(
        _k,
        CategoryConfig(
            _k, _n, _k, "standard", _q, dict(_slots), dict(_STD_TH), list(_DEFAULT_TOOLS)
        ),
    )


# Fallback used when a category cannot be classified.
FALLBACK_CATEGORY = CategoryConfig(
    "application_error",
    "General",
    "application_error",
    "standard",
    "app_support",
    {},
    {"retrieval": 0.75, "deliver": 0.78, "grounding_min": 0.72, "retry_budget": 1},
    _DEFAULT_TOOLS,
)


class CategoryRegistry:
    def __init__(self, categories: dict[str, CategoryConfig] | None = None) -> None:
        self._categories = dict(categories or DEFAULT_CATEGORIES)

    def keys(self) -> list[str]:
        return [k for k, c in self._categories.items() if c.is_active]

    def __iter__(self) -> Iterator[str]:
        """Iterate active category keys, so the registry reads like a mapping."""
        return iter(self.keys())

    def __contains__(self, category_key: object) -> bool:
        """``key in registry`` is True only for keys of *active* categories."""
        return isinstance(category_key, str) and category_key in self.keys()

    def __len__(self) -> int:
        return len(self.keys())

    def get(self, category_key: str | None) -> CategoryConfig:
        if category_key and category_key in self._categories:
            return self._categories[category_key]
        return FALLBACK_CATEGORY

    def required_slots(self, category_key: str | None) -> list[str]:
        return list(self.get(category_key).required_intake_fields.keys())

    async def load_from_db(self, session: AsyncSession) -> int:
        from sqlalchemy import select

        from app.models.registry import CategoryRegistry as CategoryRow

        result = await session.execute(select(CategoryRow).where(CategoryRow.is_active.is_(True)))
        loaded = 0
        for row in result.scalars().all():
            self._categories[row.category_key] = CategoryConfig(
                category_key=row.category_key,
                display_name=row.display_name,
                retrieval_namespace=row.retrieval_namespace,
                sla_tier=row.sla_tier,
                handoff_queue=row.handoff_queue,
                required_intake_fields=dict(row.required_intake_fields or {}),
                thresholds=dict(row.thresholds or {}),
                tool_bindings=list((row.tool_bindings or {}).get("tools", []))
                if isinstance(row.tool_bindings, dict)
                else list(row.tool_bindings or []),
                is_active=row.is_active,
            )
            loaded += 1
        return loaded


_registry: CategoryRegistry | None = None


def get_category_registry() -> CategoryRegistry:
    global _registry
    if _registry is None:
        _registry = CategoryRegistry()
    return _registry


__all__ = [
    "CategoryConfig",
    "CategoryRegistry",
    "DEFAULT_CATEGORIES",
    "FALLBACK_CATEGORY",
    "get_category_registry",
]
