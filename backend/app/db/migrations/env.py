"""Alembic migration environment.

Runs migrations with a synchronous psycopg engine built from ``core.config``
(``settings.sqlalchemy_sync_dsn``). ``target_metadata`` is the application
metadata from ``app.db.base`` (all app tables; the checkpointer-owned
``graph_checkpoints`` lives on a separate metadata and is not included), so
``--autogenerate`` diffs the ORM models against the live database.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the backend root (which contains the ``app`` package) is importable.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.core.config import get_settings  # noqa: E402
from app.db.base import target_metadata  # noqa: E402

config = context.config

# Inject the runtime DSN (never store credentials in alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_sync_dsn)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection (--sql mode)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database with a sync engine."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
