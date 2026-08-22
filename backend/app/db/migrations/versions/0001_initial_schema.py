"""Initial schema: all 36 application tables, enums, and indexes.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-06

Bootstraps the full application schema from the versioned SQLAlchemy models
(``app.db.base.target_metadata``), which are the declarative source of truth
per ARCHITECTURE.md §7.9. This guarantees the initial database matches the ORM
exactly (all native ENUM types, generated ``tsvector`` columns, and
GIN/BRIN/partial indexes are emitted from the model metadata). Subsequent schema
changes use ``alembic revision --autogenerate`` and are hand-reviewed.

The checkpointer-owned ``graph_checkpoints`` table is created separately in the
following revision via the LangGraph checkpointer's own setup routine.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.db.base import target_metadata

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Required extensions: citext (users.email), pgcrypto (gen_random_uuid on PG < 13).
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # Create every application table (SQLAlchemy resolves FK ordering and emits
    # enum types, generated columns, and all declared indexes).
    target_metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    target_metadata.drop_all(bind=bind)
