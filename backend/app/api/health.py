"""Liveness and readiness probes (mounted at the root, outside /api/v1).

- ``GET /health`` / ``GET /health/live``: liveness (process is up).
- ``GET /health/ready``: readiness -> 200 when all dependencies are reachable,
  else 503. Checks PostgreSQL, Redis, ChromaDB, the Celery broker, and whether
  the active LLM provider is configured. Check callables are module attributes
  so tests can patch them without live backends.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.redis import check_redis
from app.db.session import check_database
from app.rag.vectorstore import check_chroma
from app.workers.queue import check_celery

router = APIRouter(tags=["system"])


def _liveness_payload() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "environment": settings.APP_ENV,
    }


def providers_configured() -> bool:
    """True if the active LLM provider has a usable key (or is the fake)."""
    settings = get_settings()
    provider = settings.LLM_PROVIDER.lower()
    if provider == "fake":
        return True
    key = {
        "gemini": settings.GEMINI_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "claude": settings.ANTHROPIC_API_KEY,
    }.get(provider)
    return bool(key and key.get_secret_value())


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, Any]:
    return _liveness_payload()


@router.get("/health/live", summary="Liveness probe (alias)")
async def live() -> dict[str, Any]:
    return _liveness_payload()


@router.get("/health/ready", summary="Readiness probe (dependencies reachable)")
async def ready() -> Any:
    database, redis_ok, chroma = await asyncio.gather(
        check_database(), check_redis(), check_chroma()
    )
    celery_ok = await asyncio.to_thread(check_celery)
    checks = {
        "database": database,
        "redis": redis_ok,
        "chroma": chroma,
        "celery": celery_ok,
        "providers": providers_configured(),
    }
    healthy = all(checks.values())
    payload = {"status": "ready" if healthy else "not_ready", "checks": checks}
    if not healthy:
        return JSONResponse(status_code=503, content=payload)
    return payload


__all__ = ["router"]
