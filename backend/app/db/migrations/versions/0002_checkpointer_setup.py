"""LangGraph Postgres checkpointer setup (graph_checkpoints and friends).

Revision ID: 0002_checkpointer_setup
Revises: 0001_initial_schema
Create Date: 2026-08-06

Per ARCHITECTURE.md §7.9 the checkpointer tables are created by the LangGraph
Postgres checkpointer's own ``setup()`` routine, version-pinned here in a
dedicated revision. The saver opens its own libpq connection (independent of
Alembic's transaction) built from the runtime sync DSN.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.core.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "0002_checkpointer_setup"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Table names created by langgraph-checkpoint-postgres.
_CHECKPOINTER_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
)


def _libpq_dsn() -> str:
    """Return a plain libpq DSN (strip the SQLAlchemy '+psycopg' driver tag)."""
    return get_settings().sqlalchemy_sync_dsn.replace("postgresql+psycopg://", "postgresql://")


def upgrade() -> None:
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - dependency guaranteed in prod image
        raise RuntimeError(
            "langgraph-checkpoint-postgres must be installed to set up the "
            "checkpointer tables (revision 0002_checkpointer_setup)."
        ) from exc

    with PostgresSaver.from_conn_string(_libpq_dsn()) as checkpointer:
        checkpointer.setup()


def downgrade() -> None:
    for table in _CHECKPOINTER_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
