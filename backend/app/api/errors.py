"""Exception handlers mapping domain errors to RFC 7807 problem+json.

Registered on the FastAPI app in ``app.main``. Every response carries the
current ``trace_id`` for end-to-end correlation with logs and ``agent_runs``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.logging import get_logger, get_trace_id

_logger = get_logger(__name__)
_PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem(
    *,
    status_code: int,
    title: str,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"about:blank#{title}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "trace_id": get_trace_id(),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(status_code=status_code, content=body, media_type=_PROBLEM_MEDIA_TYPE)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return _problem(
        status_code=exc.status_code,
        title=exc.error_code,
        detail=exc.message,
        errors=exc.details if isinstance(exc.details, list) else None,
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": list(err.get("loc", [])), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return _problem(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        title="validation_error",
        detail="The request payload failed validation.",
        errors=errors,
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    response = _problem(
        status_code=exc.status_code,
        title="http_error",
        detail=str(exc.detail),
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    _logger.exception("Unhandled application error: %s", exc)
    return _problem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        title="internal_error",
        detail="An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)


__all__ = ["register_exception_handlers"]
