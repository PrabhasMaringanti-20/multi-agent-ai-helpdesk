"""Seed canonical RBAC roles and the 8 category_registry rows (idempotent).

Revision ID: 0003_seed_roles_and_categories
Revises: 0002_checkpointer_setup
Create Date: 2026-08-06

Seed data is inserted via an idempotent data-migration (upsert on the natural
key) so every environment converges deterministically, per ARCHITECTURE.md §7.9.
Payment carries stricter thresholds and a zero retry budget.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_seed_roles_and_categories"
down_revision: str | None = "0002_checkpointer_setup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROLES: list[tuple[str, str]] = [
    ("end_user", "End User"),
    ("support_engineer", "Support Engineer"),
    ("admin", "Administrator"),
    ("sme_reviewer", "SME Reviewer"),
]

# category_key, display_name, intake_fields, sla_tier, handoff_queue, thresholds
_CATEGORIES: list[dict] = [
    {
        "key": "login_issue",
        "display_name": "Login Issues",
        "intake": {"username": "string", "error_message": "string"},
        "sla_tier": "standard",
        "queue": "identity_access",
        "thresholds": {
            "retrieval": 0.72,
            "deliver": 0.75,
            "grounding_min": 0.70,
            "retry_budget": 1,
        },
    },
    {
        "key": "password_reset",
        "display_name": "Password Reset",
        "intake": {"username": "string", "account_type": "string"},
        "sla_tier": "standard",
        "queue": "identity_access",
        "thresholds": {
            "retrieval": 0.70,
            "deliver": 0.72,
            "grounding_min": 0.68,
            "retry_budget": 1,
        },
    },
    {
        "key": "vpn",
        "display_name": "VPN Problems",
        "intake": {"vpn_client": "string", "os": "string", "error_code": "string"},
        "sla_tier": "standard",
        "queue": "network_access",
        "thresholds": {
            "retrieval": 0.72,
            "deliver": 0.75,
            "grounding_min": 0.70,
            "retry_budget": 1,
        },
    },
    {
        "key": "payment",
        "display_name": "Payment Issues",
        "intake": {"invoice_id": "string", "amount": "string", "payment_method": "string"},
        "sla_tier": "priority",
        "queue": "billing",
        "thresholds": {
            "retrieval": 0.82,
            "deliver": 0.88,
            "grounding_min": 0.82,
            "retry_budget": 0,
        },
    },
    {
        "key": "software_install",
        "display_name": "Software Installation",
        "intake": {"software_name": "string", "os": "string", "version": "string"},
        "sla_tier": "standard",
        "queue": "endpoint_support",
        "thresholds": {
            "retrieval": 0.70,
            "deliver": 0.75,
            "grounding_min": 0.70,
            "retry_budget": 1,
        },
    },
    {
        "key": "application_error",
        "display_name": "Application Errors",
        "intake": {
            "application": "string",
            "error_message": "string",
            "steps_to_reproduce": "string",
        },
        "sla_tier": "standard",
        "queue": "app_support",
        "thresholds": {
            "retrieval": 0.72,
            "deliver": 0.76,
            "grounding_min": 0.72,
            "retry_budget": 1,
        },
    },
    {
        "key": "email",
        "display_name": "Email Problems",
        "intake": {"email_client": "string", "error_message": "string"},
        "sla_tier": "standard",
        "queue": "messaging",
        "thresholds": {
            "retrieval": 0.70,
            "deliver": 0.74,
            "grounding_min": 0.70,
            "retry_budget": 1,
        },
    },
    {
        "key": "hardware_request",
        "display_name": "Hardware Requests",
        "intake": {"device_type": "string", "justification": "string"},
        "sla_tier": "standard",
        "queue": "asset_management",
        "thresholds": {
            "retrieval": 0.70,
            "deliver": 0.75,
            "grounding_min": 0.70,
            "retry_budget": 1,
        },
    },
]


def upgrade() -> None:
    role_stmt = sa.text(
        "INSERT INTO roles (key, display_name) VALUES (:key, :display_name) "
        "ON CONFLICT (key) DO NOTHING"
    )
    for key, display_name in _ROLES:
        op.execute(role_stmt.bindparams(key=key, display_name=display_name))

    category_stmt = sa.text(
        """
        INSERT INTO category_registry (
            category_key, display_name, required_intake_fields, retrieval_namespace,
            sla_tier, handoff_queue, thresholds, tool_bindings, is_active
        )
        VALUES (
            :key, :display_name, CAST(:intake AS jsonb), :namespace,
            :sla_tier, :queue, CAST(:thresholds AS jsonb), CAST(:tool_bindings AS jsonb), true
        )
        ON CONFLICT (category_key) DO NOTHING
        """
    )
    for row in _CATEGORIES:
        op.execute(
            category_stmt.bindparams(
                key=row["key"],
                display_name=row["display_name"],
                intake=json.dumps(row["intake"]),
                namespace=row["key"],
                sla_tier=row["sla_tier"],
                queue=row["queue"],
                thresholds=json.dumps(row["thresholds"]),
                tool_bindings=json.dumps({}),
            )
        )


def downgrade() -> None:
    category_keys = [row["key"] for row in _CATEGORIES]
    op.execute(
        sa.text("DELETE FROM category_registry WHERE category_key IN :keys").bindparams(
            sa.bindparam("keys", value=tuple(category_keys), expanding=True)
        )
    )
    role_keys = [key for key, _ in _ROLES]
    op.execute(
        sa.text("DELETE FROM roles WHERE key IN :keys").bindparams(
            sa.bindparam("keys", value=tuple(role_keys), expanding=True)
        )
    )
