"""HTTP middleware: request/trace correlation and non-enforcing auth context.

Per ARCHITECTURE.md §5.1/§5.2, request correlation lives in middleware while
authentication *enforcement* lives in the DI dependencies (``get_current_user`` /
``require_roles`` / ``require_permissions``). ``AuthContextMiddleware`` therefore
only decodes a present bearer token (no DB access) to enrich logs/audit context;
it never rejects a request. Rate limiting (Redis-backed) is added in the Core
milestone.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import get_settings
from app.core.constants import REQUEST_ID_HEADER, TRACE_ID_HEADER, TokenType
from app.core.logging import bind_context, clear_context, get_logger, get_trace_id
from app.core.redis import get_redis_client
from app.core.security import TokenError, decode_token

_logger = get_logger("app.request")
_audit_logger = get_logger("app.audit")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach request-id + trace-id, bind them to logging, and time the request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex
        bind_context(request_id=request_id, trace_id=trace_id)
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        start = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000
            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[TRACE_ID_HEADER] = trace_id
            response.headers["X-Process-Time-ms"] = f"{duration_ms:.2f}"
            _logger.info(
                "%s %s -> %s (%.2f ms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            return response
        except Exception:
            _logger.exception("%s %s -> unhandled error", request.method, request.url.path)
            raise
        finally:
            clear_context()


class AuthContextMiddleware(BaseHTTPMiddleware):
    """Decode a present bearer token for observability only (no enforcement)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.auth = None
        header = request.headers.get("authorization")
        if header and header.lower().startswith("bearer "):
            token = header[7:].strip()
            try:
                decoded = decode_token(token, expected_type=TokenType.ACCESS)
                request.state.auth = {
                    "user_id": decoded.subject,
                    "org_id": decoded.org_id,
                    "role": decoded.role,
                }
            except TokenError:
                request.state.auth = None
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-minute rate limiter backed by Redis.

    Keyed by authenticated user id when present, else client IP. Fails OPEN: if
    Redis is unavailable the request proceeds (logged), so a cache outage never
    takes down the API. Probe/doc paths and CORS preflight are exempt.
    """

    _SKIP_PREFIXES = ("/health", "/docs", "/redoc", "/openapi", "/favicon")

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        settings = get_settings()
        self.enabled = settings.RATE_LIMIT_ENABLED
        self.limit = settings.RATE_LIMIT_PER_MINUTE

    def _skip(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._SKIP_PREFIXES)

    def _identity(self, request: Request) -> str:
        auth = getattr(request.state, "auth", None)
        if auth and auth.get("user_id"):
            return f"user:{auth['user_id']}"
        host = request.client.host if request.client else "anonymous"
        return f"ip:{host}"

    def _too_many(self, retry_after: int) -> JSONResponse:
        body: dict[str, Any] = {
            "type": "about:blank#rate_limited",
            "title": "rate_limited",
            "status": 429,
            "detail": "Rate limit exceeded. Please retry later.",
            "trace_id": get_trace_id(),
        }
        return JSONResponse(
            status_code=429,
            content=body,
            media_type="application/problem+json",
            headers={"Retry-After": str(retry_after)},
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or request.method == "OPTIONS" or self._skip(request.url.path):
            return await call_next(request)

        window = int(time.time() // 60)
        key = f"ratelimit:{self._identity(request)}:{window}"
        try:
            client = get_redis_client()
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, 60)
            if count > self.limit:
                retry_after = 60 - int(time.time() % 60)
                return self._too_many(retry_after)
        except Exception as exc:  # noqa: BLE001 - fail open on cache outage
            _logger.warning("Rate limiter unavailable (fail-open): %s", exc)
        return await call_next(request)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Emit a structured audit-trail log line for every mutating request.

    Durable, semantic audit (before/after diffs) is written to ``audit_logs`` by
    the service layer via ``services.audit_service``; this middleware provides the
    lightweight, always-on HTTP audit trail keyed by actor + trace id.
    """

    _MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if request.method in self._MUTATING:
            auth = getattr(request.state, "auth", None) or {}
            _audit_logger.info(
                "http_audit %s %s -> %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "event_type": "http_audit",
                    "http_method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "actor_id": auth.get("user_id"),
                    "actor_role": auth.get("role"),
                },
            )
        return response


__all__ = [
    "RequestContextMiddleware",
    "AuthContextMiddleware",
    "RateLimitMiddleware",
    "AuditLogMiddleware",
]
