# Backend

FastAPI service for the Multi-Agent AI Helpdesk: the HTTP API, the LangGraph agent engine,
retrieval, and all database access.

See the [root README](../README.md) for the one-command local setup, and
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the full design.

## Layout

Requests flow downward; nothing ever reaches back up a layer.

| Package | Responsibility |
|---|---|
| `app/api/` | Routers, request/response wiring, auth dependencies, DI (`deps.py`) |
| `app/services/` | Business logic — chat, tickets, KB drafting, data API, document search |
| `app/agents/` | The agent engine: `state.py`, `graph.py`, `routing.py`, and `nodes/` (14 nodes) |
| `app/rag/` | Chunking, file parsers, sparse + dense retrieval, RRF fusion, reranking |
| `app/providers/` | LLM and embedding adapters behind one interface, plus the fallback chain |
| `app/repositories/` | Data access — the only layer that issues SQL |
| `app/models/` | SQLAlchemy models (40 tables) |
| `app/registries/` | Data-driven config: categories, prompt templates, thresholds, tools |
| `app/core/` | Settings, security/JWT, RBAC, middleware, exceptions, logging |
| `app/workers/` | Optional Celery tasks (ingestion) |

Two rules keep the layering honest: **only repositories touch the database**, and **provider
SDKs are imported lazily inside their adapter**, so an unused provider never needs to be
installed.

## Running it directly

```bash
python -m venv .venv && .venv/Scripts/activate     # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                # add your model API key

PYTHONPATH=. python scripts/seed_demo.py            # schema + demo data
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

## Scripts

| Script | What it does |
|---|---|
| `scripts/seed_demo.py` | Creates the schema and seeds roles, org, users, categories, 93 KB articles, tickets, notifications |
| `scripts/reindex_kb.py` | Re-embeds published KB chunks into the vector index (resumable, rate-limit aware) |
| `scripts/check_providers.py` | One live call per configured provider to confirm keys and models work |
| `scripts/bootstrap_admin.py` | Creates the first admin user (used by the Docker path) |

## Tests

```bash
pytest -q                  # 62 unit tests, no API key or network needed
ruff check . && mypy app
```

Tests run against the `fake` LLM and embedding providers, so they're deterministic.

## Migrations

Alembic is configured against `app.db.base.target_metadata`:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

The local `start.py` path uses `create_all` for speed; Docker and any real deployment use
migrations.
