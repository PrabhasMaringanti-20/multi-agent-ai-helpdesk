"""Celery application (Redis broker) — the async background-task framework.

Per ARCHITECTURE.md §5.6, background/event-triggered work runs on Celery with a
Redis broker + result backend. Domain task modules (learning, ingestion,
notification, analytics) are registered in later milestones and auto-discovered
from the ``app.workers`` package; this module owns the app, its configuration,
the shared base task (JSON-only, bounded autoretry), and a health ``ping`` task.
"""

from __future__ import annotations

from typing import Any

from celery import Celery, Task

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

_logger = get_logger(__name__)


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "helpdesk",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        result_expires=3600,
    )
    # Domain task modules are added in later milestones; discovery is future-proof.
    app.autodiscover_tasks(["app.workers"])
    return app


celery_app = create_celery_app()


class BaseTask(Task):
    """Shared base: JSON-only, bounded exponential autoretry on transient errors."""

    autoretry_for = (Exception,)
    max_retries = 3
    retry_backoff = True
    retry_backoff_max = 60
    retry_jitter = True


@celery_app.on_after_configure.connect
def _setup_worker_logging(sender: Celery, **_: Any) -> None:  # pragma: no cover
    configure_logging()


@celery_app.task(base=BaseTask, name="app.workers.ping")
def ping() -> str:
    """Liveness task used to validate broker/worker connectivity."""
    _logger.info("celery ping received")
    return "pong"


def check_celery() -> bool:
    """Best-effort broker reachability check for readiness; never raises."""
    try:
        with celery_app.connection() as connection:
            connection.ensure_connection(max_retries=1, timeout=2)
        return True
    except Exception as exc:  # noqa: BLE001 - readiness must not raise
        _logger.warning("Celery broker readiness check failed: %s", exc)
        return False


__all__ = ["celery_app", "create_celery_app", "BaseTask", "ping", "check_celery"]
