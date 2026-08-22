"""ASGI application factory.

Mounts the full middleware stack, exception handlers, health/readiness probes,
and the versioned API router. Later milestones extend the lifespan (ChromaDB
client warmup, LangGraph graph compilation) and append v1 routers.
"""

from __future__ import annotations

# Trust the OS certificate store process-wide (handles corporate TLS inspection)
# before any HTTPS client is created. Best-effort: falls back to certifi.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    AuditLogMiddleware,
    AuthContextMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from app.core.redis import close_redis
from app.db.session import dispose_engine

_logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _logger.info("Starting %s v%s (%s)", settings.APP_NAME, settings.VERSION, settings.APP_ENV)
    yield
    await close_redis()
    await dispose_engine()
    _logger.info("Shutdown complete; Redis and database connections disposed.")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    # Added inner-first; the LAST added is outermost. Resulting request order:
    # CORS -> RequestContext -> AuthContext -> RateLimit -> AuditLog -> app.
    app.add_middleware(AuditLogMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthContextMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_app()

__all__ = ["app", "create_app"]
