"""Idempotent demo bootstrap: create an organization + an admin user.

Runs after migrations (which seed roles + categories). Gated by BOOTSTRAP_DEMO
in the entrypoint so it never runs in a real production deployment. Credentials
come from env with dev-only defaults.

    BOOTSTRAP_ORG_SLUG        (default: acme)
    BOOTSTRAP_ORG_NAME        (default: Acme Corp)
    BOOTSTRAP_ADMIN_EMAIL     (default: admin@acme.com)
    BOOTSTRAP_ADMIN_PASSWORD  (default: ChangeMe123!)
"""

from __future__ import annotations

import asyncio
import os
import sys

# Make the `app` package importable when run as `python scripts/bootstrap_admin.py`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.constants import RoleKey  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionFactory, dispose_engine  # noqa: E402
from app.models.organization import Organization, Role  # noqa: E402
from app.models.user import User  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def _run() -> None:
    org_slug = os.getenv("BOOTSTRAP_ORG_SLUG", "acme")
    org_name = os.getenv("BOOTSTRAP_ORG_NAME", "Acme Corp")
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@acme.com")
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "ChangeMe123!")

    async with SessionFactory() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(name=org_name, slug=org_slug, settings={}, is_active=True)
            session.add(org)
            await session.flush()
            print(f"[bootstrap] created organization '{org_slug}'")
        else:
            print(f"[bootstrap] organization '{org_slug}' already exists")

        admin_role = (
            await session.execute(select(Role).where(Role.key == RoleKey.ADMIN.value))
        ).scalar_one_or_none()
        if admin_role is None:
            print("[bootstrap] ERROR: roles not seeded — run 'alembic upgrade head' first")
            return

        existing = (
            await session.execute(select(User).where(User.org_id == org.id, User.email == email))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                User(
                    org_id=org.id,
                    email=email,
                    hashed_password=hash_password(password),
                    full_name="Demo Admin",
                    role_id=admin_role.id,
                    is_active=True,
                )
            )
            print(f"[bootstrap] created admin user '{email}'")
        else:
            print(f"[bootstrap] admin user '{email}' already exists")

        await session.commit()

    await dispose_engine()
    print(f"[bootstrap] SIGN IN WITH -> org: {org_slug}  email: {email}  password: {password}")


if __name__ == "__main__":
    asyncio.run(_run())
