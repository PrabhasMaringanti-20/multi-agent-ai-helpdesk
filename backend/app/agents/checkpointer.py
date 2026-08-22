"""LangGraph checkpointer binding (durable per-thread state; §7.3).

Returns an in-memory saver by default (dev/tests) and a Postgres-backed saver
when a DSN is supplied (production), enabling durable ``human_handoff``
interrupts and reconnect/resume on the same ``thread_id``. Imports are lazy so
this module loads without LangGraph installed.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


def build_memory_checkpointer() -> Any:
    """In-memory checkpointer (dev/tests)."""
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def build_postgres_checkpointer(dsn: str | None = None) -> Any:
    """Create a Postgres checkpointer context manager for production use.

    Returns the ``PostgresSaver`` context manager (call ``.setup()`` once, then
    keep it open for the app's lifetime). Falls back to the in-memory saver if
    the Postgres extra is unavailable.
    """
    resolved = dsn or get_settings().sqlalchemy_sync_dsn.replace(
        "postgresql+psycopg://", "postgresql://"
    )
    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        return PostgresSaver.from_conn_string(resolved)
    except ImportError:  # pragma: no cover
        _logger.warning("langgraph-checkpoint-postgres unavailable; using MemorySaver.")
        return build_memory_checkpointer()


__all__ = ["build_memory_checkpointer", "build_postgres_checkpointer"]
