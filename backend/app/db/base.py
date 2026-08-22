"""Metadata import surface for Alembic autogenerate.

Alembic's ``env.py`` imports this module so that every ORM model is registered
on ``Base.metadata`` before autogenerate compares the model metadata against the
live database. Importing the ``app.models`` package (which imports every model
module) is what actually populates the metadata; the star import below is the
explicit, lint-visible surface.

The checkpointer-owned ``graph_checkpoints`` table lives on ``CheckpointBase``
and is deliberately excluded from ``Base.metadata`` (see models/checkpoint.py).
"""

from __future__ import annotations

from app.models import *  # noqa: F401,F403  (register all tables on Base.metadata)
from app.models.base import Base

# The single metadata object Alembic targets for application migrations.
target_metadata = Base.metadata

__all__ = ["Base", "target_metadata"]
