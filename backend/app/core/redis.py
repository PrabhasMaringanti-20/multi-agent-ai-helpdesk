"""Redis client factory and health check.

Redis is required infrastructure (ARCHITECTURE.md §7 / §11.1): it backs the
answer/memory cache, rate-limit counters, the JWT ``jti`` denylist, and the
Celery broker. The client is created lazily (no connection at import) and reused
process-wide; it is closed on application shutdown.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

_logger = get_logger(__name__)
_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    """Return the shared async Redis client (created on first use)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
            # Fail fast when Redis is unavailable so a cache outage never adds
            # multi-second latency to requests (the caller fails open).
            socket_connect_timeout=0.5,
            socket_timeout=1.0,
            retry_on_timeout=False,
        )
    return _client


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency yielding the shared Redis client."""
    return get_redis_client()


async def check_redis() -> bool:
    """Best-effort readiness ping; never raises."""
    try:
        client = get_redis_client()
        return bool(await client.ping())
    except Exception as exc:  # noqa: BLE001 - readiness must not raise
        _logger.warning("Redis readiness check failed: %s", exc)
        return False


async def close_redis() -> None:
    """Close the client pool (called on application shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# --------------------------------------------------------------------------- #
# Caching + session/denylist helpers (Phase 4)
# --------------------------------------------------------------------------- #
async def cache_get(key: str) -> str | None:
    try:
        return await get_redis_client().get(key)
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        _logger.warning("cache_get failed: %s", exc)
        return None


async def cache_set(key: str, value: str, *, ttl_seconds: int = 3600) -> None:
    try:
        await get_redis_client().set(key, value, ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("cache_set failed: %s", exc)


async def deny_jti(jti: str, *, ttl_seconds: int) -> None:
    """Add a token id to the revocation denylist (forced-logout session store)."""
    try:
        await get_redis_client().set(f"denylist:{jti}", "1", ex=max(1, ttl_seconds))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("deny_jti failed: %s", exc)


async def is_jti_denied(jti: str) -> bool:
    try:
        return bool(await get_redis_client().exists(f"denylist:{jti}"))
    except Exception as exc:  # noqa: BLE001 - fail open (do not lock users out on cache outage)
        _logger.warning("is_jti_denied failed: %s", exc)
        return False


__all__ = [
    "get_redis_client",
    "get_redis",
    "check_redis",
    "close_redis",
    "cache_get",
    "cache_set",
    "deny_jti",
    "is_jti_denied",
]
