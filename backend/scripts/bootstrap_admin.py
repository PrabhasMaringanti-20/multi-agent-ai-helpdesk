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

        roles = await session.execute(select(Role).where(Role.key.in_([RoleKey.ADMIN.value, RoleKey.SUPPORT_ENGINEER.value, RoleKey.SME_REVIEWER.value])))
        role_map = {r.key: r for r in roles.scalars().all()}
        
        if RoleKey.ADMIN.value not in role_map:
            print("[bootstrap] ERROR: roles not seeded")
            return
            
        users_to_create = [
            (email, password, "Demo Admin", role_map[RoleKey.ADMIN.value].id),
            ("support@acme.com", "ChangeMe123!", "Demo Support", role_map[RoleKey.SUPPORT_ENGINEER.value].id),
            ("sme@acme.com", "ChangeMe123!", "Demo SME", role_map[RoleKey.SME_REVIEWER.value].id),
        ]
        
        for u_email, u_password, u_name, u_role_id in users_to_create:
            existing = (
                await session.execute(select(User).where(User.org_id == org.id, User.email == u_email))
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    User(
                        org_id=org.id,
                        email=u_email,
                        hashed_password=hash_password(u_password),
                        full_name=u_name,
                        role_id=u_role_id,
                        is_active=True,
                    )
                )
                print(f"[bootstrap] created user '{u_email}'")
            else:
                print(f"[bootstrap] user '{u_email}' already exists")

        await session.commit()

    await dispose_engine()
    print(f"[bootstrap] SIGN IN WITH -> org: {org_slug}  email: {email}  password: {password}")


if __name__ == "__main__":
    asyncio.run(_run())
