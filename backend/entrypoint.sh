#!/usr/bin/env bash
# Container entrypoint. Waits for infrastructure, then starts the requested role.
#   api    -> run migrations, then serve the FastAPI app (uvicorn)
#   worker -> Celery worker (learning / ingestion / notification / analytics tasks)
#   beat   -> Celery beat scheduler
#   migrate-> run Alembic migrations only (one-shot)
set -euo pipefail

ROLE="${1:-api}"

wait_for_services() {
  python - <<'PY'
import sys
import time

from app.core.config import get_settings

settings = get_settings()

# --- PostgreSQL ---
import psycopg  # noqa: E402

dsn = settings.sqlalchemy_sync_dsn.replace("postgresql+psycopg://", "postgresql://")
for attempt in range(60):
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        print("postgres: ready", flush=True)
        break
    except Exception as exc:  # noqa: BLE001
        print(f"postgres: waiting ({exc})", flush=True)
        time.sleep(2)
else:
    sys.exit("postgres did not become ready")

# --- Redis ---
import redis  # noqa: E402

client = redis.from_url(settings.REDIS_URL)
for attempt in range(30):
    try:
        client.ping()
        print("redis: ready", flush=True)
        break
    except Exception as exc:  # noqa: BLE001
        print(f"redis: waiting ({exc})", flush=True)
        time.sleep(2)
else:
    sys.exit("redis did not become ready")
PY
}

case "${ROLE}" in
  api)
    wait_for_services
    echo "Running database migrations..."
    alembic upgrade head
    if [ "${BOOTSTRAP_DEMO:-false}" = "true" ]; then
      echo "Seeding demo organization + admin user..."
      python scripts/bootstrap_admin.py || echo "bootstrap skipped (non-fatal)"
    fi
    echo "Starting Celery worker in background for free-tier compatibility..."
    celery -A app.workers.queue.celery_app worker --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-2}" &
    echo "Starting API..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
    ;;
  worker)
    wait_for_services
    exec celery -A app.workers.queue.celery_app worker --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-4}"
    ;;
  beat)
    wait_for_services
    exec celery -A app.workers.queue.celery_app beat --loglevel=INFO
    ;;
  migrate)
    wait_for_services
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown role: ${ROLE} (expected api|worker|beat|migrate)" >&2
    exit 64
    ;;
esac
