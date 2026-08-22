"""Async SQLAlchemy engine, session factory, and request-scoped session dependency.

Per ARCHITECTURE.md §5.2, ``get_session`` yields a request-scoped
``AsyncSession`` that auto-commits on success and rolls back on error. The
engine is created once at import (module-level singleton) using the async DSN
from ``core.config`` and is disposed at application shutdown via ``dispose_engine``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.sqlalchemy_async_dsn,
    echo=_settings.DB_ECHO,
    pool_pre_ping=True,
    pool_size=_settings.DB_POOL_SIZE,
    max_overflow=_settings.DB_MAX_OVERFLOW,
    pool_timeout=_settings.DB_POOL_TIMEOUT,
    future=True,
)

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a request-scoped session with commit/rollback.

    The session commits when the request handler returns normally and rolls back
    on any exception, then always closes. Repositories operate on this session;
    services own transaction boundaries where finer control is needed.
    """
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the connection pool (call on application shutdown)."""
    await engine.dispose()


async def check_database() -> bool:
    """Best-effort readiness check: open a connection and run ``SELECT 1``."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - readiness must not raise
        return False


__all__ = [
    "engine",
    "SessionFactory",
    "get_session",
    "dispose_engine",
    "check_database",
]
