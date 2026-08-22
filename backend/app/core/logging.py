"""Structured JSON logging with request/trace correlation.

Per ARCHITECTURE.md §4, ``core.logging`` provides structured JSON logging with
``trace_id`` / ``request_id`` correlation. Correlation ids are propagated with
``contextvars`` so they attach automatically to every log record emitted during
a request or a graph run, without threading them through call signatures.

The middleware layer (delivered in the Core folder) sets the request id; the
LangGraph orchestrator sets the trace id (``AgentState.trace_id``). Both default
to ``"-"`` outside a request so logs never crash on missing context.
"""

from __future__ import annotations

import json
import logging
import logging.config
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------- #
# Correlation context
# --------------------------------------------------------------------------- #
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")


def set_request_id(request_id: str) -> None:
    _request_id_ctx.set(request_id)


def get_request_id() -> str:
    return _request_id_ctx.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id)


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def bind_context(*, request_id: str | None = None, trace_id: str | None = None) -> None:
    """Set any provided correlation ids on the current context."""
    if request_id is not None:
        _request_id_ctx.set(request_id)
    if trace_id is not None:
        _trace_id_ctx.set(trace_id)


def clear_context() -> None:
    """Reset correlation ids (call at the end of a request/task)."""
    _request_id_ctx.set("-")
    _trace_id_ctx.set("-")


# --------------------------------------------------------------------------- #
# Filter + formatter
# --------------------------------------------------------------------------- #
class ContextFilter(logging.Filter):
    """Inject correlation ids onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        record.trace_id = _trace_id_ctx.get()
        return True


# Attributes that already exist on a stdlib LogRecord; anything else passed via
# ``logger.info(..., extra={...})`` is treated as a structured field.
_RESERVED_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "request_id",
        "trace_id",
    }
)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "trace_id": getattr(record, "trace_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def configure_logging(
    *,
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """Configure root + framework loggers. Idempotent; safe to call at startup.

    Reads defaults from ``core.config`` when arguments are omitted.
    """
    from app.core.config import get_settings

    settings = get_settings()
    resolved_level = (level or settings.LOG_LEVEL).upper()
    use_json = settings.LOG_JSON if json_output is None else json_output
    handler_formatter = "json" if use_json else "console"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "context": {"()": "app.core.logging.ContextFilter"},
            },
            "formatters": {
                "json": {"()": "app.core.logging.JsonFormatter"},
                "console": {
                    "format": (
                        "%(asctime)s | %(levelname)-8s | %(name)s | "
                        "trace=%(trace_id)s req=%(request_id)s | %(message)s"
                    ),
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": handler_formatter,
                    "filters": ["context"],
                },
            },
            "root": {
                "level": resolved_level,
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {
                    "level": resolved_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": resolved_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": resolved_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "sqlalchemy.engine": {
                    "level": "WARNING",
                    "handlers": ["default"],
                    "propagate": False,
                },
                "celery": {
                    "level": resolved_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Use ``get_logger(__name__)`` at import time."""
    return logging.getLogger(name)


__all__ = [
    "ContextFilter",
    "JsonFormatter",
    "configure_logging",
    "get_logger",
    "set_request_id",
    "get_request_id",
    "set_trace_id",
    "get_trace_id",
    "bind_context",
    "clear_context",
]
