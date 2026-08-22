# Enterprise AI Helpdesk — Code Walkthrough

A detailed, file-by-file explanation of the whole codebase, written for a developer
who wants to understand exactly what each part does and how it fits together.
It goes near line-by-line on the logic that matters and file-level on boilerplate.

## The mental model (read this first)

**What it is:** an AI IT helpdesk. A user asks a question in chat → the AI answers from a
knowledge base (grounded, cited) or, if the KB has nothing, from general knowledge →
if it still can't help, it raises a support ticket to a human. Engineers/admins manage
KB articles, see dashboards, and chat with users. Two AI extras: an **AI Data API**
(ask the database in plain English) and **Document Search** (upload files → AI-search them).

**Backend layers (a request flows down, the answer flows back up):**

| Layer | Folder | Job |
|-------|--------|-----|
| API | `app/api/` | HTTP endpoints (routers), `deps.py` = auth + dependency injection, `main.py` = startup |
| Services | `app/services/` | Business logic |
| AI engine | `app/agents/` | The LangGraph "brain" — nodes + graph + routing that run per chat turn |
| RAG | `app/rag/` | Search the knowledge base (keyword + vector + rerank) |
| Providers | `app/providers/` | LLM/embedding adapters (Gemini/OpenAI/Claude/fake) behind one interface |
| Repositories | `app/repositories/` | Data access — the only layer that touches the DB |
| Models | `app/models/` | Database tables (ORM) |
| Core | `app/core/` | Config, security/JWT, constants, middleware |
| Registries | `app/registries/` | Data-driven config: categories, thresholds, prompts, tools |

**The key flow (a chat question):** `ChatPage` → `POST /api/v1/chat/messages` (SSE) →
`chat.py` runs `engine.astream` → the LangGraph walks: ingress_guard → memory(load) →
intent_classifier → query_planner → rag_retriever (KB + uploaded files) → retrieval_gate →
solution_synthesizer (grounded or general) → grounding_verifier → confidence_gate →
responder (or ticket_creator → human_handoff) → memory(persist) → the answer streams back
token-by-token with citations.


## Contents

1. Backend — Core & Config
2. Backend — Data Models
3. Backend — DB Session & Repositories
4. Backend — Providers & Registries
5. Backend — RAG Pipeline
6. Backend — AI Engine core (LangGraph)
7. Backend — AI Engine nodes
8. Backend — Services
9. Backend — API layer
10. Frontend (React SPA)
11. Scripts, Infra & Runbook

---

## Backend — Core & Config

This section walks through the `backend/app/core` package and the application entry point `backend/app/main.py`. The `core` package is the framework-agnostic foundation of the whole platform: configuration, constants, security, RBAC, logging, middleware, exceptions, Redis access, and small utilities. A guiding rule you'll see repeated is that these modules avoid importing FastAPI, the ORM, or the database, so the exact same code can be reused by the web API, background workers (Celery), and CLI scripts. `main.py` is the one place that wires everything together into a running web app.

---

### `core/__init__.py`

**Purpose**
Marks `app.core` as a package and re-exports the three most commonly used config symbols so other modules can write short imports.

**How it works**
It does almost nothing except forward the config essentials:

```python
from app.core.config import Settings, get_settings, settings
__all__ = ["Settings", "get_settings", "settings"]
```

Because of this, another module can write `from app.core import settings` instead of the longer `from app.core.config import settings`. The docstring restates the architectural contract: `core` owns configuration, security, RBAC, logging, middleware, exceptions, and shared constants/enums, and it is "framework-agnostic."

**Connects to**
Directly re-exports from `config.py`. Every layer that needs settings can lean on this convenience.

---

### `config.py` — Pydantic settings

**Purpose**
The single, canonical place where all runtime configuration lives. It reads values from environment variables / a `.env` file, validates them strictly (fail-fast), and exposes a cached singleton `settings` used everywhere.

**How it works**

It uses **Pydantic v2 `BaseSettings`** (from `pydantic_settings`), which is the modern "12-factor config" pattern: every field is typed, and each field can be overridden by an environment variable of the same name.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

- `env_file=(".env",)` — load a local `.env` if present.
- `case_sensitive=False` — `SECRET_KEY` and `secret_key` both match.
- `extra="ignore"` — unknown env vars don't crash the app.

The class then declares grouped fields with sensible development defaults: application (`APP_NAME`, `APP_ENV`, `DEBUG`, `API_V1_PREFIX`, `VERSION`), JWT/security, PostgreSQL, Redis, Celery, ChromaDB, LLM/embedding providers, generation tuning, retrieval/memory knobs, CORS, rate limiting, and logging.

Notice **secrets are wrapped in `SecretStr`**:

```python
SECRET_KEY: SecretStr = SecretStr("dev-insecure-local-secret-key-change-me-0123456789")
POSTGRES_PASSWORD: SecretStr = SecretStr("helpdesk")
GEMINI_API_KEY: SecretStr = SecretStr("")
```

`SecretStr` prevents the value from being accidentally printed in logs or tracebacks (it shows `**********`). To read the real value you must call `.get_secret_value()`, which the code does deliberately in a few places.

`APP_ENV` is typed as the `Environment` enum imported from `constants.py`, not a plain string. That means only `local/development/staging/production` are valid.

**Validators** run at construction time. There are three field validators with `mode="before"` (they see the raw env value before type coercion):

```python
@field_validator("APP_ENV", mode="before")
def _normalize_env(cls, value): ...      # lower-cases "PRODUCTION" -> "production"

@field_validator("LOG_LEVEL", mode="before")
def _normalize_level(cls, value): ...    # upper-cases "info" -> "INFO"
```

The CORS validator is the interesting one, and it pairs with a special field annotation:

```python
CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
    default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
)
```

`NoDecode` turns off pydantic-settings' automatic JSON parsing so the validator can accept either format from the environment:

```python
@field_validator("CORS_ORIGINS", mode="before")
def _split_origins(cls, value):
    # "[...]" -> json.loads;  "a,b,c" -> ["a","b","c"]
```

So an operator can set `CORS_ORIGINS=https://a.com,https://b.com` (comma list) or a JSON array — both work.

The **fail-fast production check** is a `model_validator(mode="after")` (it runs once, after all fields are set):

```python
@model_validator(mode="after")
def _enforce_production_safety(self) -> Settings:
    if self.APP_ENV != Environment.PRODUCTION:
        return self
    secret = self.SECRET_KEY.get_secret_value()
    if secret in _INSECURE_DEV_SECRETS or len(secret) < 32:
        raise ValueError("SECRET_KEY must be a unique random value of >= 32 chars in production.")
    if self.LLM_PROVIDER == "gemini" and not self.GEMINI_API_KEY.get_secret_value():
        raise ValueError("GEMINI_API_KEY is required in production when LLM_PROVIDER=gemini.")
    return self
```

Outside production it returns immediately (dev defaults are fine). In production it refuses to boot if you shipped one of the known insecure dev secrets (`_INSECURE_DEV_SECRETS`), a too-short key, or a missing Gemini key while Gemini is the active provider. This is the "raise at construction time rather than starting in an unsafe state" behavior promised in the module docstring.

**Derived values** use `@computed_field` / `@property` so callers don't build connection strings by hand:

```python
@computed_field
@property
def sqlalchemy_async_dsn(self) -> str:
    password = quote_plus(self.POSTGRES_PASSWORD.get_secret_value())
    return f"postgresql+asyncpg://{self.POSTGRES_USER}:{password}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
```

There are two DSNs: `sqlalchemy_async_dsn` uses the `asyncpg` driver for the running app, and `sqlalchemy_sync_dsn` uses `psycopg` for Alembic migrations. Note `quote_plus` URL-encodes the password so special characters (`@`, `/`, `:`) don't break the DSN. There are also `chroma_url`, `is_production`, and `is_local` convenience properties.

Finally, the **cached singleton**:

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings: Settings = get_settings()
```

`@lru_cache(maxsize=1)` guarantees `Settings()` is constructed exactly once per process; every `get_settings()` call returns the same object. The module also eagerly builds `settings` at import time — safe because every field has a dev default and production safety is enforced by the validator.

**Connects to**
Imports `Environment` from `constants.py`. It is imported by nearly everything: `security.py` (JWT secret/algorithm/expiry, issuer/audience), `redis.py` (`REDIS_URL`), `logging.py` (`LOG_LEVEL`, `LOG_JSON`), `middleware.py` (rate-limit settings), and `main.py` (app title, version, CORS, API prefix). The DSN properties feed `db/session.py` and Alembic.

---

### `constants.py` — enums (single source of truth)

**Purpose**
Defines every enumerated value used across the platform in one file, so the database ENUM types, the ORM models, the Pydantic API schemas, and the orchestrator can never drift apart.

**How it works**
Every enum subclasses Python 3.12's `StrEnum`:

```python
from enum import StrEnum

class TicketStatus(StrEnum):
    OPEN = "open"
    TRIAGED = "triaged"
    ...
```

`StrEnum` means each member **is** its string value — `TicketStatus.OPEN == "open"` is `True`, and it serializes to JSON as `"open"` with no extra work. That property is what lets these enums map cleanly onto SQLAlchemy `Enum` columns and appear directly in API responses.

The file is organized by domain area, and the docstrings tie each enum back to a specific database column (e.g. "`tickets.status`"), which is a strong signal these mirror `DATABASE_DESIGN.md`:

- **Platform/infra:** `Environment` (drives the config fail-fast check), `TokenType` (`access`/`refresh`), `VectorStore`.
- **Identity/RBAC:** `RoleKey` (the four seed roles), `ActorType` (`user`/`agent`/`system`/`worker`).
- **Multi-agent orchestration:** `Decision` (`deliver`/`clarify`/`retry_retrieval`/`escalate` — the confidence-gate outcomes), `SensitivityLevel`, `AgentExecStatus`.
- **Conversation/memory:** `ConversationStatus`, `MessageRole`.
- **Ticketing/handoff:** `TicketStatus`, `TicketPriority`, `TicketEventType`, `AttachmentKind`, `AssignmentReason`, `EscalationType`, `EscalationTrigger`, `NoteVisibility`.
- **KB/ingestion:** `DocStatus`, `SourceType`, `IngestionTrigger`, `IngestionStatus`, `ApprovalDecision`.
- **Feedback/learning:** `FeedbackRating`, `LearningTrigger`, `LearningStatus`.
- **Notifications/files:** `NotificationChannel`, `NotificationType`, `NotificationStatus`, `ScanStatus`, `FilePurpose`.
- **Analytics/admin:** `SettingScope`, `PeriodGrain`.

At the bottom are two plain string constants used for HTTP correlation headers:

```python
REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"
```

The explicit `__all__` list controls what `from app.core.constants import *` exposes.

**Connects to**
`config.py` uses `Environment`; `security.py` uses `TokenType`; `rbac.py` uses `RoleKey`; `middleware.py` uses `REQUEST_ID_HEADER`, `TRACE_ID_HEADER`, and `TokenType`. The ticketing/KB/notification enums are consumed by ORM models and service layers elsewhere in the app.

---

### `security.py` — JWT + Argon2

**Purpose**
Low-level security primitives: password hashing/verification, JWT creation and validation, and refresh-token helpers. It has zero web/DB dependencies so workers and CLI tools can use it too.

**How it works**

**Password hashing** uses passlib with Argon2 as the default and bcrypt kept only for verifying older hashes:

```python
_pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
```

```python
def hash_password(password): return _pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        return False
```

`verify_password` is careful: a malformed or unknown hash is treated as a failed check, never an exception — so a corrupt stored value can't crash login. `password_needs_rehash` calls `needs_update`, which returns `True` when a stored hash uses a deprecated scheme (e.g. an old bcrypt hash) so the app can transparently upgrade it on the user's next successful login.

**Typed error classes** and **typed result dataclasses** make the API tidy:

```python
class SecurityError(Exception): ...
class TokenError(SecurityError): ...

@dataclass(frozen=True, slots=True)
class IssuedToken:      # token + jti + type + issued_at + expires_at
@dataclass(frozen=True, slots=True)
class DecodedToken:     # subject, token_type, jti, org_id, role, iat, exp, raw claims
```

`frozen=True` makes them immutable; `slots=True` makes them memory-efficient. The caller gets back not just the token string but the metadata it must persist (the `jti`, expiry) for session tracking.

**Token id helpers:**

```python
def generate_jti(): return uuid.uuid4().hex               # unique JWT id for denylist/session
def generate_refresh_secret(): return secrets.token_urlsafe(48)
def hash_refresh_token(token): return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

The `jti` (JWT ID) is what makes revocation possible — it's stored in Redis' denylist. Refresh tokens are stored server-side only as a SHA-256 hash (never in plaintext), which enables rotation and reuse detection.

**JWT encoding** is centralized in a private `_encode` helper:

```python
payload = {
    "sub": subject,
    "type": token_type.value,   # "access" or "refresh"
    "jti": jti,
    "iat": issued_at, "nbf": issued_at, "exp": expires_at,
    "iss": settings.JWT_ISSUER,
    "aud": settings.JWT_AUDIENCE,
}
...
token = jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
```

Every token carries standard registered claims: subject, issued-at, not-before, expiry, issuer, and audience — plus the custom `type` and `jti`. Extra claims are merged in but `None` values are filtered out so you never emit `"org_id": null`.

`create_access_token` and `create_refresh_token` are thin wrappers that set the right expiry from settings (`ACCESS_TOKEN_EXPIRE_MINUTES` vs `REFRESH_TOKEN_EXPIRE_DAYS`) and pack `org_id`/`role` into the claims.

**Decoding** is where validation happens:

```python
payload = jwt.decode(
    token,
    settings.SECRET_KEY.get_secret_value(),
    algorithms=[settings.JWT_ALGORITHM],
    audience=settings.JWT_AUDIENCE,
    issuer=settings.JWT_ISSUER,
    options={"require": ["exp", "iat", "sub", "jti", "type"]},
)
```

The library checks the signature, expiry (`exp`), not-before (`nbf`), audience, and issuer, and the `require` option rejects any token missing a mandatory claim. Failures are converted into the module's own `TokenError`:

```python
except jwt.ExpiredSignatureError as exc:
    raise TokenError("Token has expired.") from exc
except jwt.InvalidTokenError as exc:
    raise TokenError("Token is invalid.") from exc
```

It then validates the `type` against the `TokenType` enum, optionally enforces an `expected_type` (so a refresh token can't be used where an access token is required), and returns a fully typed `DecodedToken` with `iat`/`exp` converted back into timezone-aware `datetime`s.

**Connects to**
Reads JWT settings from `config.py`; uses `TokenType` from `constants.py`. Its `decode_token` and `TokenError` are used by `middleware.py` (`AuthContextMiddleware`) for non-enforcing token decoding. The docstring notes the real enforcement and session persistence live in `services.auth_service` / `api.deps`, and the `jti` denylist is handled by `redis.py`.

---

### `rbac.py` — permissions matrix

**Purpose**
The authoritative, in-code map of "which role can do what." It defines permission strings and the role → permission grants for the four seed roles.

**How it works**

Permissions are namespaced constants in the `resource:action` format:

```python
class Permission:
    CHAT_USE = "chat:use"
    TICKET_READ = "ticket:read"
    KB_PUBLISH = "kb:publish"
    AUDIT_READ = "audit:read"
    ADMIN_MANAGE_USERS = "admin:manage_users"
    ...
```

The full catalog is built automatically by reflecting over the class attributes — so you never have to maintain a separate list:

```python
ALL_PERMISSIONS = frozenset(
    value for name, value in vars(Permission).items()
    if not name.startswith("_") and isinstance(value, str)
)
```

The role grants are built **incrementally using set union**, which nicely encodes the role hierarchy:

```python
_END_USER_PERMISSIONS = frozenset({CHAT_USE, CONVERSATION_READ, ..., FILE_UPLOAD})

_SUPPORT_ENGINEER_PERMISSIONS = _END_USER_PERMISSIONS | {TICKET_WRITE, TICKET_ASSIGN, ...}

_SME_REVIEWER_PERMISSIONS = _SUPPORT_ENGINEER_PERMISSIONS | {KB_REVIEW, KB_PUBLISH}

_ADMIN_PERMISSIONS = ALL_PERMISSIONS      # admin = everything, incl. audit:read + admin:*
```

So a support engineer automatically gets everything an end user has, plus ticket-management and KB-write powers; an SME reviewer adds KB review/publish; and admin is a strict superset of every permission. `frozenset` makes these immutable so they can't be mutated at runtime.

The lookup table and helpers translate a role *string* (as it appears in a JWT) into permissions:

```python
ROLE_PERMISSIONS = { RoleKey.END_USER: _END_USER_PERMISSIONS, ... }

def permissions_for_role(role_key: str) -> frozenset[str]:
    try:
        return ROLE_PERMISSIONS[RoleKey(role_key)]
    except ValueError:
        return frozenset()      # unknown role -> no permissions (safe default)

def role_has_permission(role_key, permission):
    return permission in permissions_for_role(role_key)
```

Note the safe fallback: an unrecognized role key returns an empty set rather than raising, so a bad/stale role grants nothing.

**Connects to**
Imports `RoleKey` from `constants.py`. The docstring says this matrix is the source used to derive the `roles.permissions` JWT fast-path cache and to answer checks in `api.deps.require_permissions`. It pairs with the `role` claim that `security.py` embeds in tokens.

---

### `exceptions.py` — domain error hierarchy

**Purpose**
A framework-agnostic set of business exceptions that services and the orchestrator raise. The API layer later maps them to HTTP responses, but these classes themselves know nothing about HTTP.

**How it works**
A single base class carries the metadata needed to build an RFC 7807 problem response:

```python
class AppError(Exception):
    status_code: int = 500
    error_code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(self, message=None, *, details=None):
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)
```

Each subclass just overrides the three class attributes, which keeps them extremely small:

```python
class ValidationError(AppError):      status_code = 422; error_code = "validation_error"
class AuthenticationError(AppError):  status_code = 401; error_code = "authentication_error"
class ForbiddenError(AppError):       status_code = 403; error_code = "forbidden"
class NotFoundError(AppError):        status_code = 404; error_code = "not_found"
class ConflictError(AppError):        status_code = 409; error_code = "conflict"
class ProviderError(AppError):        status_code = 502; error_code = "provider_error"
class RetrievalError(AppError):       status_code = 503; error_code = "retrieval_error"
```

`ProviderError` (502) is for a failing upstream LLM/embedding provider, and `RetrievalError` (503) is for the knowledge-retrieval subsystem being down — both map to the multi-agent pipeline. The optional `details` field can carry structured field errors.

**Connects to**
`validation.py` raises `ValidationError`. The `api/errors.py` module (registered by `main.py`) catches `AppError` and reads `status_code` / `error_code` / `message` / `details` to build the JSON problem body. Because there are no FastAPI imports here, Celery workers and LangGraph nodes can raise the same errors without a web context.

---

### `redis.py` — client factory + cache/denylist helpers

**Purpose**
Manages a single shared async Redis connection and provides small helpers for caching, the JWT denylist, and health checks. Redis is treated as required infrastructure (answer/memory cache, rate-limit counters, `jti` denylist, Celery broker).

**How it works**
It uses `redis.asyncio` and a module-level singleton created **lazily** (no connection is opened at import):

```python
_client: aioredis.Redis | None = None

def get_redis_client() -> aioredis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=0.5,
            socket_timeout=1.0,
            retry_on_timeout=False,
        )
    return _client
```

The short timeouts are a deliberate design choice: if Redis is down the calls fail *fast* (half a second) instead of hanging for seconds, and callers "fail open." `decode_responses=True` means you get back `str` instead of `bytes`.

`get_redis` is the FastAPI dependency form; `check_redis` is a best-effort readiness ping that swallows exceptions and returns a bool (readiness probes must never raise); `close_redis` calls `aclose()` and resets the singleton to `None` on shutdown.

The Phase-4 helpers all share the same **fail-soft** pattern — they log a warning and return a safe default instead of propagating errors:

```python
async def cache_get(key):        # returns None on failure
async def cache_set(key, value, *, ttl_seconds=3600):
async def deny_jti(jti, *, ttl_seconds):    # SET denylist:{jti} with expiry
async def is_jti_denied(jti):    # returns False on cache outage -> don't lock users out
```

The comment on `is_jti_denied` is important security reasoning: on a cache outage it returns `False` (fail open) so a Redis blip doesn't lock every user out. Conversely `deny_jti` uses `max(1, ttl_seconds)` so the TTL is always at least 1 second.

**Connects to**
Reads `REDIS_URL` from `config.py`; logs via `logging.py`. Used by `middleware.py`'s `RateLimitMiddleware` (`incr`/`expire`). `close_redis` is called from `main.py`'s lifespan shutdown. The `deny_jti`/`is_jti_denied` pair works with the `jti` produced in `security.py`.

---

### `logging.py` — structured JSON logging with correlation

**Purpose**
Configures application-wide logging and attaches a `request_id` and `trace_id` to every log line so you can follow a single request across many log entries.

**How it works**

Correlation ids are stored in **`contextvars`**, which are per-task/per-request variables that don't need to be passed through every function signature:

```python
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")
```

The default `"-"` means logs emitted outside a request (startup, workers) still render cleanly instead of crashing on missing context. Helpers `set_request_id`, `get_request_id`, `set_trace_id`, `get_trace_id`, `bind_context`, and `clear_context` read/write those vars.

A logging **Filter** copies the current context ids onto each record:

```python
class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id_ctx.get()
        record.trace_id = _trace_id_ctx.get()
        return True
```

The **JSON formatter** renders each record as a single-line JSON object and, cleverly, promotes any `extra={...}` fields into top-level JSON keys:

```python
payload = {"timestamp": ..., "level": ..., "logger": ..., "message": ...,
           "request_id": ..., "trace_id": ...}
...
for key, value in record.__dict__.items():
    if key not in _RESERVED_ATTRS and not key.startswith("_"):
        payload[key] = value
return json.dumps(payload, default=str, ensure_ascii=False)
```

`_RESERVED_ATTRS` is a frozenset of stdlib `LogRecord` attribute names; anything *not* in that set is assumed to be a custom structured field a caller passed via `extra=`. This is exactly how `AuditLogMiddleware` gets `event_type`, `actor_id`, etc. into the log. `default=str` ensures non-serializable objects (like `datetime`) don't blow up.

`configure_logging` builds the whole logging tree via `dictConfig`:

```python
def configure_logging(*, level=None, json_output=None):
    from app.core.config import get_settings   # local import avoids a cycle
    settings = get_settings()
    resolved_level = (level or settings.LOG_LEVEL).upper()
    use_json = settings.LOG_JSON if json_output is None else json_output
    handler_formatter = "json" if use_json else "console"
    logging.config.dictConfig({...})
```

It reads `LOG_LEVEL`/`LOG_JSON` from config (arguments can override), then registers the `context` filter, both formatters (`json` and a human-readable `console` format), a single stdout stream handler, and tames the noisy framework loggers — `uvicorn`, `uvicorn.error`, `uvicorn.access`, `sqlalchemy.engine` (pinned to `WARNING`), and `celery` — each with `propagate: False` so messages aren't duplicated. Note the **local import of `get_settings` inside the function** — this deliberately breaks a potential circular import, since other core modules import `get_logger` at module load. `get_logger(name)` is just a thin wrapper over `logging.getLogger`.

**Connects to**
Reads `LOG_LEVEL`/`LOG_JSON` from `config.py`. `get_logger` is imported all over `core` (`middleware`, `redis`). `bind_context`/`clear_context`/`get_trace_id` are used by `middleware.py`; `get_trace_id` also powers the `trace_id` field in `api/errors.py` problem responses. `configure_logging` is called once in `main.create_app`.

---

### `utils.py` — tiny shared helpers

**Purpose**
A handful of dependency-free utilities used across layers.

**How it works**

```python
def utcnow() -> datetime:
    return datetime.now(timezone.utc)          # always timezone-aware UTC

def chunked(sequence, size):                   # yields size-length lists
    if size <= 0:
        raise ValueError("size must be a positive integer")
    for start in range(0, len(sequence), size):
        yield list(sequence[start:start + size])

def coalesce(*values):                         # first non-None, else None
    for value in values:
        if value is not None:
            return value
    return None
```

`utcnow` is the project's standard "now" so timestamps are consistently timezone-aware (avoids the classic naive-vs-aware datetime bugs). `chunked` is handy for batching, e.g. sending embedding requests in groups. `coalesce` mimics SQL's `COALESCE`. They use a `TypeVar` `_T` so type checkers keep the element type. Nothing here imports anything project-specific.

**Connects to**
Pure standalone helpers; usable anywhere with no risk of import cycles. Likely consumed by services, the ingestion pipeline (`chunked`), and models (`utcnow`).

---

### `validation.py` — input validation helpers

**Purpose**
Small validators that raise the domain `ValidationError` (not a raw `ValueError`), so bad input surfaces consistently as an RFC 7807 `422` response.

**How it works**

```python
def parse_uuid(value, *, field="id") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"'{field}' must be a valid UUID.") from exc

def require(condition, message):               # guard clause -> ValidationError
    if not condition:
        raise ValidationError(message)

def clamp(value, low, high):                   # constrain to [low, high]
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, high))

def normalize_str(value):                      # trim; "" -> None
    if value is None:
        return None
    return value.strip() or None
```

The key idea: `parse_uuid` and `require` raise `ValidationError` (a domain error the API knows how to translate into a 422 problem), whereas `clamp`'s programmer-error guard (`low > high`) raises a plain `ValueError` because that's a bug, not bad user input. `normalize_str` collapses whitespace-only strings to `None`, which is convenient for optional text fields.

**Connects to**
Imports `ValidationError` from `exceptions.py`. Used by services/routers; the resulting `ValidationError` is caught by `api/errors.py` and rendered as a `422` problem response.

---

### `middleware.py` — HTTP middleware stack

**Purpose**
Cross-cutting request behavior: correlation ids + timing, non-enforcing auth context for observability, Redis-backed rate limiting, and a lightweight HTTP audit trail. Enforcement of auth lives in the DI dependencies, not here.

**How it works**
All four classes subclass Starlette's `BaseHTTPMiddleware` and implement `async def dispatch`.

**1. `RequestContextMiddleware`** — the outermost useful layer:

```python
request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
trace_id   = request.headers.get(TRACE_ID_HEADER)   or uuid.uuid4().hex
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
    _logger.info("%s %s -> %s (%.2f ms)", ...)
    return response
except Exception:
    _logger.exception(...)
    raise
finally:
    clear_context()
```

It reuses an incoming correlation id if the client sent one (useful for tracing across services) or mints a new hex id. It binds them into the logging context (so every downstream log line gets them), stashes them on `request.state`, times the request, echoes the ids and a processing-time header back on the response, and — crucially — `clear_context()` in `finally` resets the contextvars so ids never leak into the next request handled on the same worker.

**2. `AuthContextMiddleware`** — decodes but never rejects:

```python
request.state.auth = None
header = request.headers.get("authorization")
if header and header.lower().startswith("bearer "):
    token = header[7:].strip()
    try:
        decoded = decode_token(token, expected_type=TokenType.ACCESS)
        request.state.auth = {"user_id": decoded.subject, "org_id": decoded.org_id, "role": decoded.role}
    except TokenError:
        request.state.auth = None
return await call_next(request)
```

This is *observability only*: it decodes a valid access token to enrich logs/audit with the actor, but an invalid/missing token just leaves `request.state.auth = None` and the request continues. Real 401/403 enforcement happens later in `api.deps`. Note it makes no DB call — pure JWT decode.

**3. `RateLimitMiddleware`** — fixed-window Redis limiter:

```python
def __init__(self, app):
    super().__init__(app)
    settings = get_settings()
    self.enabled = settings.RATE_LIMIT_ENABLED
    self.limit = settings.RATE_LIMIT_PER_MINUTE
```

It reads config once at construction. In `dispatch` it skips when disabled, on `OPTIONS` (CORS preflight), or for exempt prefixes (`/health`, `/docs`, `/redoc`, `/openapi`, `/favicon`). The identity is the authenticated user if present, otherwise the client IP:

```python
window = int(time.time() // 60)
key = f"ratelimit:{self._identity(request)}:{window}"
count = await client.incr(key)
if count == 1:
    await client.expire(key, 60)
if count > self.limit:
    return self._too_many(retry_after)
```

The `window` bucket changes every 60 seconds; the first request in a window sets a 60s expiry. Over the limit returns a `429` in `application/problem+json` (matching the app's error format) with a `Retry-After` header and the current `trace_id`. Importantly it **fails open**: any Redis exception is caught and logged, and the request proceeds — a cache outage never takes the API down.

**4. `AuditLogMiddleware`** — always-on HTTP audit trail:

```python
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
...
response = await call_next(request)
if request.method in self._MUTATING:
    auth = getattr(request.state, "auth", None) or {}
    _audit_logger.info("http_audit %s %s -> %s", ..., extra={
        "event_type": "http_audit", "http_method": ..., "path": ...,
        "status_code": ..., "actor_id": auth.get("user_id"), "actor_role": auth.get("role")})
```

It logs a structured line for every mutating request, pulling actor info from the `request.state.auth` that `AuthContextMiddleware` set upstream. The `extra=` fields flow straight into the JSON log via `JsonFormatter`. This is the lightweight trail; the durable before/after audit lives in `audit_logs` via `services.audit_service`.

**Connects to**
Uses `decode_token`/`TokenError` from `security.py`, `REQUEST_ID_HEADER`/`TRACE_ID_HEADER`/`TokenType` from `constants.py`, `bind_context`/`clear_context`/`get_logger`/`get_trace_id` from `logging.py`, `get_redis_client` from `redis.py`, and `get_settings` from `config.py`. All four are registered (in a specific order) by `main.py`. The `request.state.auth` set here is consumed downstream by rate limiting, auditing, and the DI enforcement layer.

---

### `main.py` — app factory, lifespan, middleware order

**Purpose**
The ASGI entry point. It builds the FastAPI app, configures logging, mounts the middleware stack in the correct order, registers exception handlers, wires up health and versioned routers, and defines startup/shutdown behavior.

**How it works**

Before any other imports run, it tries to trust the OS certificate store — useful behind corporate TLS interception:

```python
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
```

It's best-effort: if `truststore` isn't installed it silently falls back to certifi. This is placed at the very top so it takes effect before any HTTPS client is created.

**Lifespan** uses the modern `@asynccontextmanager` pattern (code before `yield` runs at startup, after `yield` runs at shutdown):

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    _logger.info("Starting %s v%s (%s)", settings.APP_NAME, settings.VERSION, settings.APP_ENV)
    yield
    await close_redis()
    await dispose_engine()
    _logger.info("Shutdown complete; ...")
```

On shutdown it cleanly disposes the Redis pool (`close_redis`) and the DB engine (`dispose_engine`). The docstring notes later milestones will add ChromaDB warmup and LangGraph compilation here.

**The app factory** `create_app`:

```python
def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title=settings.APP_NAME, version=settings.VERSION,
        docs_url="/docs", redoc_url="/redoc",
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
```

Note logging is configured *first* so even early startup logs are structured. Config drives the title/version and the OpenAPI URL (mounted under the API prefix).

**Middleware order** is the subtlest and most important part. Starlette wraps middleware so the **last added is the outermost** (runs first on the way in, last on the way out). The code adds them inner-first and documents the resulting request flow:

```python
# Resulting request order:
# CORS -> RequestContext -> AuthContext -> RateLimit -> AuditLog -> app.
app.add_middleware(AuditLogMiddleware)          # added first  = innermost
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthContextMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])   # added last = outermost
```

Why this order matters:
- **CORS is outermost** so even error responses (and preflight `OPTIONS`) get CORS headers.
- **RequestContext next** so correlation ids and timing wrap essentially the entire pipeline and every downstream log carries them.
- **AuthContext before RateLimit and AuditLog** because both of those read `request.state.auth` — auth must be decoded first so rate limiting can key by user id and auditing can record the actor.
- **AuditLog innermost** so it observes the final handler status code.

CORS `allow_origins` comes straight from the validated `CORS_ORIGINS` setting.

Then it registers handlers and routers:

```python
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

app = create_app()
```

`register_exception_handlers` (from `api/errors.py`) attaches handlers for `AppError`, FastAPI's `RequestValidationError`, Starlette's `HTTPException`, and a catch-all `Exception` — all rendered as `application/problem+json` with the current `trace_id`. Health/readiness probes are mounted at the root (so they line up with the rate-limiter's `/health` skip prefix), and the versioned `api_router` is mounted under `/api/v1`. Finally `app = create_app()` builds the module-level ASGI app that uvicorn imports.

**Connects to**
This is the hub. It imports and wires: all four middleware classes from `middleware.py`; `configure_logging`/`get_logger` from `logging.py`; `get_settings` from `config.py`; `close_redis` from `redis.py`; `dispose_engine` from `db.session`; `register_exception_handlers` from `api/errors.py` (which reads the `exceptions.py` hierarchy); and the `health` and `api.v1.router` routers. The middleware ordering documented here is what makes the correlation, auth-context, rate-limit, and audit features in `middleware.py` behave correctly together.

---

**Big picture:** `config.py` and `constants.py` are the two roots everything else depends on. `security.py`, `rbac.py`, `exceptions.py`, `redis.py`, `logging.py`, `utils.py`, and `validation.py` are independent, framework-free building blocks. `middleware.py` composes several of them into request-time behavior, and `main.py` assembles the whole thing — configuring logging, choosing a deliberate middleware order, mapping the `exceptions.py` hierarchy to HTTP via `api/errors.py`, and managing resource lifecycle through the lifespan.

---

## Backend — Data Models

This section walks through every SQLAlchemy ORM model in `backend/app/models`. These files are the Python mirror of the PostgreSQL database. Each Python class becomes one database table, each `Mapped[...]` attribute becomes one column, and `relationship(...)` calls describe how tables link so you can navigate between rows as normal Python objects.

Before diving in, here are three ideas that recur everywhere:

- **Declarative mapping.** Every model inherits from a `Base` class. SQLAlchemy scans the class, reads the type hints (`Mapped[str]`, `Mapped[uuid.UUID]`, …), and builds the table definition automatically.
- **Mixins.** Small helper classes (like `TimestampMixin`) add the same columns to many tables without copy-pasting. A model just lists them in its parent list: `class User(Base, TenantMixin, TimestampMixin, SoftDeleteMixin)`.
- **Multi-tenancy.** This is a SaaS product serving many customer companies ("tenants"). Almost every row carries an `org_id` so one company can never see another's data.

---

### `base.py` — the shared foundation

**Purpose:** Defines the single declarative `Base` that all tables share, the reusable mixins (UUID primary key, timestamps, soft-delete, tenancy), a custom case-insensitive text type, and — importantly — the native PostgreSQL `ENUM` type objects that every other model file imports. It encodes the project's database conventions in one place so they stay consistent.

**How it works:**

The file starts with a **naming convention** dictionary:

```python
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    ...
    "pk": "pk_%(table_name)s",
}
```

Why does this matter? When you don't name an index or constraint explicitly, the database (or Alembic, the migration tool) has to invent a name. Without a rule, those invented names can be random or unstable, which makes migrations noisy and hard to review. This template forces predictable names like `ix_users_email` or `pk_users`. The comment calls it "autogenerate-friendly" — meaning Alembic's automatic migration generator will produce stable, diff-able output.

Next, a **custom column type**:

```python
class CITEXT(UserDefinedType):
    cache_ok = True
    def get_col_spec(self, **kw: Any) -> str:
        return "CITEXT"
```

`CITEXT` is PostgreSQL's "case-insensitive text." SQLAlchemy doesn't ship a built-in mapping for it, so this tiny class tells SQLAlchemy to emit the literal SQL type `CITEXT`. It's used for `users.email` so that `Alice@Corp.com` and `alice@corp.com` are treated as the same address. `cache_ok = True` tells SQLAlchemy this type is safe to cache in its compiled-statement cache.

Then the **declarative base**:

```python
class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: sa.TIMESTAMP(timezone=True),
        uuid.UUID: PgUUID(as_uuid=True),
        str: sa.Text(),
        dict[str, Any]: sa.dialects.postgresql.JSONB,
        list[Any]: sa.dialects.postgresql.JSONB,
    }
```

`Base` is the parent of every application table. Two things happen here:
1. Its `metadata` carries the naming convention, so every table built from this base inherits those naming rules.
2. `type_annotation_map` is a lookup table that converts a **Python type hint** into a **database column type**. Because of this, a model can write `created_at: Mapped[datetime]` and SQLAlchemy automatically uses a timezone-aware `TIMESTAMP`; a plain `Mapped[str]` becomes `TEXT` (not a length-limited `VARCHAR`); and any `dict`/`list` field becomes `JSONB` (PostgreSQL's binary JSON, which supports indexing and querying inside the JSON). This is why later models rarely spell out column types — the map does it for them.

The **UUID primary key helper**:

```python
UUIDpk = Annotated[
    uuid.UUID,
    mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
]
```

This is a clever reuse trick. `UUIDpk` is an `Annotated` type: it means "a `uuid.UUID`, but with these column settings attached." Any model can then declare its primary key in one line — `id: Mapped[UUIDpk]` — and get a UUID primary key whose default value is generated **by the database** via `gen_random_uuid()`. `server_default` means the database fills it in (not Python), so IDs exist even for rows inserted outside the app.

The **four mixins** each add specific columns:

```python
class CreatedAtMixin:      # created_at only, indexed — for append-only tables
class TimestampMixin:      # created_at + updated_at (auto-touched on update)
class SoftDeleteMixin:     # nullable deleted_at — "soft delete" flag
class TenantMixin:         # org_id FK to organizations, indexed — tenancy
```

- `CreatedAtMixin` adds one immutable `created_at` with `server_default=func.now()` (the DB stamps the time). It's for "write-once" tables like logs and events.
- `TimestampMixin` adds both `created_at` and `updated_at`. The key line is `onupdate=func.now()` on `updated_at`: every time the row changes, the timestamp refreshes automatically.
- `SoftDeleteMixin` adds a nullable `deleted_at`. "Soft delete" means rows aren't physically removed — a non-null `deleted_at` marks them as deleted, so data can be recovered and audited.
- `TenantMixin` adds `org_id` as a foreign key to `organizations.id` with `ondelete="CASCADE"` (delete an org and all its rows follow) and an index. The docstring notes it "leads composite indexes" — because tenant queries always filter by `org_id` first, it's the natural first column in multi-column indexes.

Finally, the **ENUM factory and bindings**:

```python
def _pg_enum(enum_cls: type, name: str) -> PgEnum:
    return PgEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=True,
        values_callable=lambda ec: [member.value for member in ec],
        validate_strings=True,
    )
```

This builds a **native PostgreSQL `ENUM` type** from a Python `StrEnum` (defined in `constants.py`). Key details for an early-career reader:
- `native_enum=True` means the database gets a real `ENUM` type (a fixed list of allowed string values enforced by the DB), not just a `VARCHAR`.
- `values_callable=lambda ec: [member.value for member in ec]` is crucial: it stores each member's **value** (e.g. `"open"`), not its Python name (`OPEN`). Without this, SQLAlchemy would store the uppercase member name and the database would drift from the JSON-facing string values.
- `create_type=True` lets SQLAlchemy create the type in the DB automatically.

The rest of the file is just dozens of one-liners calling `_pg_enum(...)` — `TICKET_STATUS_ENUM`, `DOC_STATUS_ENUM`, `FEEDBACK_RATING_ENUM`, etc. The file's docstring explains *why they live here*: a shared enum like `doc_status` is used by both `kb_documents` and `kb_chunks`. Defining the enum type object **once** here and importing it in both places guarantees PostgreSQL creates that type exactly once, instead of trying to create it twice and erroring.

**Connects to:** This is the root dependency of the whole package. Every other model file imports `Base`, `UUIDpk`, the mixins, and the relevant `*_ENUM` objects from here. It in turn imports `app.core.constants` (aliased `c`) to get the Python enum classes.

---

### `__init__.py` — the package assembler

**Purpose:** Importing `app.models` pulls in every model class so they all register themselves on `Base.metadata`. This is what makes Alembic and `create_all` see the full schema.

**How it works:** The file simply imports every model from every sibling module and re-exports them in `__all__`. The docstring flags one deliberate exception:

> the checkpointer-owned `graph_checkpoints` lives on a separate metadata (`CheckpointBase`) and is intentionally not part of `Base.metadata`.

So even though `GraphCheckpoint` is imported here for convenience, it belongs to a different metadata and is excluded from the app's own migrations (more on that in `checkpoint.py`).

**Connects to:** It is the single import surface for the rest of the backend (services, repositories, migrations). Anything that needs a model imports it from `app.models`.

---

### `organization.py` — tenants and RBAC (roles/permissions)

**Purpose:** Defines the tenant root (`Organization`) and the role-based access control tables: `Role`, `Permission`, and the `RolePermission` join table.

**How it works:**

`Organization` is the top of the tenancy tree:

```python
class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[UUIDpk]
    name: Mapped[str] = mapped_column(nullable=False)
    slug: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, ...)
    is_active: Mapped[bool] = mapped_column(..., server_default=text("true"))
    users: Mapped[list[User]] = relationship(back_populates="users")
```

- `slug` is a URL-safe unique handle (e.g. `acme-corp`), indexed and unique for fast lookups.
- `settings` is a free-form `JSONB` blob defaulting to an empty object `'{}'::jsonb` — per-org config that doesn't deserve its own columns.
- `users` is a **relationship**: it doesn't add a column; it lets you write `org.users` in Python to get every user in that org. `back_populates="organization"` pairs it with the matching attribute on `User`.

`Role` holds seeded RBAC roles:

```python
class Role(Base, TimestampMixin):
    key: Mapped[str] = mapped_column(..., unique=True, index=True)
    permissions: Mapped[list[Any]] = mapped_column(JSONB, ...)   # denormalized cache
    role_permissions: Mapped[list[RolePermission]] = relationship(...)
    granted_permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", viewonly=True
    )
```

The design here is worth understanding. There are **two** ways this model tracks permissions:
1. `permissions` (a JSONB list) — the docstring calls it "the denormalized JWT cache." When a user logs in, the app can copy this list straight into their JWT token without any joins. Fast, but denormalized (duplicated data).
2. The normalized `role_permissions` link rows — the real source of truth.

`granted_permissions` is a convenience read-only view: `secondary="role_permissions"` tells SQLAlchemy to hop *through* the join table to reach `Permission` rows, and `viewonly=True` means you can read `role.granted_permissions` but not modify the links through it (you edit `role_permissions` directly instead).

`Permission` is the normalized catalog:

```python
class Permission(Base, CreatedAtMixin):
    key: Mapped[str] = mapped_column(..., unique=True, index=True)
    resource: Mapped[str] = mapped_column(..., index=True)
    action: Mapped[str] = mapped_column(nullable=False)
```

Each permission is a `resource` + `action` pair (e.g. resource `ticket`, action `assign`), with a unique `key` as the human-readable identifier.

`RolePermission` is the many-to-many bridge:

```python
class RolePermission(Base, CreatedAtMixin):
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), ...)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), ...)
```

The `UniqueConstraint("role_id", "permission_id", ...)` prevents granting the same permission to the same role twice. Both foreign keys `CASCADE` on delete, so removing a role or permission cleans up its links automatically. Its two relationships (`role`, `permission`) let you navigate both directions.

**Connects to:** `User` (in `user.py`) points its `role_id` at `roles` and its `org_id` at `organizations`. Almost every other table's `TenantMixin.org_id` ultimately points back to `Organization`. `RolePermission` sits between `Role` and `Permission`.

---

### `user.py` — users and sessions

**Purpose:** Defines `User` (platform accounts) and `UserSession` (the refresh-token ledger that powers secure login and token rotation).

**How it works:**

```python
class User(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_users_org_id_email"),
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), ...)
    last_login_at: Mapped[datetime | None] = ...
```

Several teaching points:
- `User` stacks **three mixins**: it's tenant-scoped (`org_id`), fully timestamped, and soft-deletable.
- The unique constraint is on `(org_id, email)`, **not** email alone. The same email can exist in two different orgs, but not twice within one org. This is the multi-tenant flavor of "unique email."
- `email` uses the `CITEXT` type from `base.py` so it's case-insensitive.
- `hashed_password` stores only the hash — never a plaintext password.
- `role_id` uses `ondelete="RESTRICT"`: you cannot delete a role while users still reference it. This protects against accidentally orphaning users' permissions.
- The `organization`, `role`, and `sessions` relationships wire the object graph. `sessions` uses `cascade="all, delete-orphan"` so deleting a user deletes their sessions too.

```python
class UserSession(Base, CreatedAtMixin):
    refresh_token_hash: Mapped[str] = mapped_column(..., unique=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    expires_at: Mapped[datetime] = ...
    revoked_at: Mapped[datetime | None] = ...
```

The docstring says this supports "JWT rotation + reuse detection." How that works: each login issues a refresh token; only its **hash** is stored (`refresh_token_hash`, unique). When a client refreshes, the server looks up the hash; if a *revoked* token is reused, that signals theft. `ip_address` uses PostgreSQL's native `INET` type (proper IP-address storage). `expires_at` is indexed to make expiry sweeps fast. Deleting the user cascades to the session (FK `ondelete="CASCADE"`).

**Connects to:** `User.org_id → organizations`, `User.role_id → roles`. `UserSession.user_id → users`. `User` is one of the most-referenced tables — conversations, tickets, feedback, notifications, audit logs, and more all foreign-key back to it.

---

### `conversation.py` — chat threads and memory

**Purpose:** Models the conversational core: `Conversation` (a chat thread), `Message` (individual turns), `ConversationSummary` (rolling long-term summaries), and `MemoryFact` (durable per-user facts).

**How it works:**

```python
class Conversation(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __table_args__ = (
        Index("ix_conversations_user_last_message", "user_id", "last_message_at"),
    )
    status: Mapped[ConversationStatus] = mapped_column(
        CONVERSATION_STATUS_ENUM, ..., server_default=ConversationStatus.ACTIVE.value, index=True
    )
    category: Mapped[str | None] = mapped_column(
        ForeignKey("category_registry.category_key", ondelete="SET NULL"), ...
    )
    last_message_at: Mapped[datetime | None] = ...
```

- The docstring reveals a key architectural fact: **`id` doubles as the LangGraph `thread_id`.** LangGraph is the orchestration engine; its checkpointer keys everything by `thread_id`, and here that's simply the conversation's primary key.
- `status` uses the shared `CONVERSATION_STATUS_ENUM` and defaults to `active`. Note the `server_default=ConversationStatus.ACTIVE.value` — `.value` yields the string `"active"` that the DB enum expects.
- `category` is a foreign key to `category_registry.category_key` (a *string* PK, unusual — see `registry.py`), with `ondelete="SET NULL"` so removing a category just clears the field.
- The composite index `(user_id, last_message_at)` optimizes the common "list my conversations, newest first" query.

```python
class Message(Base, CreatedAtMixin):
    __table_args__ = (
        Index("ix_messages_conversation_turn", "conversation_id", "turn_id"),
        Index("ix_messages_trace_id", "trace_id"),
    )
    turn_id: Mapped[int] = ...
    role: Mapped[MessageRole] = mapped_column(MESSAGE_ROLE_ENUM, ...)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, ...)
    decision: Mapped[Decision | None] = mapped_column(DECISION_ENUM, ...)
    trace_id: Mapped[str] = ...
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, ...)
```

`Message` is append-only (only `CreatedAtMixin` — messages are never edited). The docstring notes it's "source for `add_messages` hydration," i.e. when the graph rebuilds its message list, it reads these rows. `turn_id` orders turns within a thread; `role` is who spoke (user/assistant/system/tool); `citations` and `token_usage` are JSONB side-data; `decision` records the routing decision (deliver/clarify/retry/escalate) attached to an assistant turn; `trace_id` links a message to its observability trace (indexed for lookups).

```python
class ConversationSummary(Base, CreatedAtMixin):
    __table_args__ = (
        Index(
            "uq_conversation_summaries_current",
            "conversation_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    is_current: Mapped[bool] = mapped_column(..., server_default=text("true"), index=True)
```

This is your first **partial unique index** — an important PostgreSQL pattern. The unique constraint on `conversation_id` only applies `postgresql_where=text("is_current")`, i.e. only among rows where `is_current` is true. Meaning: a conversation can have *many* historical summary rows, but only **one** current one. This lets the app keep version history (`version`, `covered_through_turn`) while enforcing "exactly one active summary" at the database level.

```python
class MemoryFact(Base, TenantMixin, TimestampMixin):
    __table_args__ = (
        UniqueConstraint("user_id", "fact_key", name="uq_memory_facts_user_key"),
    )
    confidence: Mapped[float] = mapped_column(..., server_default=text("1.0"))
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), ...
    )
    expires_at: Mapped[datetime | None] = ...
```

`MemoryFact` stores durable facts about a user (e.g. "prefers Python") that survive across conversations. The `(user_id, fact_key)` unique constraint means one value per fact key per user (upsert semantics). `confidence` defaults to `1.0`; `source_conversation_id` records where the fact was learned (nulled if that conversation is deleted); `expires_at` allows facts to age out.

**Connects to:** `Conversation.user_id → users`, `Conversation.category → category_registry`. `Message` and `ConversationSummary` belong to `Conversation` (cascade delete). `MemoryFact` links to `users` and optionally `conversations`. Downstream, `tickets`, `feedback`, `agent_runs`, `escalations`, and `analytics_events` all reference `conversations`.

---

### `ticket.py` — tickets and human handoff

**Purpose:** The largest model file: `Ticket` (an engineer-ready support ticket) plus its satellite tables `TicketEvent`, `TicketAttachment`, `EngineerNote`, `TicketAssignment`, and `Escalation`.

**How it works:**

```python
class Ticket(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __table_args__ = (
        Index("ix_tickets_queue_status_priority", "assigned_queue", "status", "priority"),
        Index("ix_tickets_engineer_status", "assigned_engineer_id", "status"),
        Index(
            "ix_tickets_sla_due_open",
            "sla_due_at",
            postgresql_where=text("status NOT IN ('resolved', 'closed')"),
        ),
        Index("gin_tickets_intake_fields", "intake_fields", postgresql_using="gin"),
    )
```

Four indexes tuned to real query patterns:
- `(assigned_queue, status, priority)` — the support-queue dashboard view.
- `(assigned_engineer_id, status)` — "my open tickets."
- A **partial index** on `sla_due_at` that only covers tickets *not* resolved/closed — the SLA monitor only cares about live tickets, so the index stays small.
- A **GIN index** on `intake_fields` (`postgresql_using="gin"`). GIN indexes make it fast to query *inside* a JSONB document (e.g. "tickets where intake_fields has key X").

Key columns:

```python
conversation_id: Mapped[uuid.UUID] = mapped_column(
    ForeignKey("conversations.id", ondelete="RESTRICT"), ..., unique=True, index=True
)
assigned_engineer_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("users.id", ondelete="SET NULL"), ...
)
category: Mapped[str] = mapped_column(
    ForeignKey("category_registry.category_key", ondelete="RESTRICT"), nullable=False, index=True
)
priority: Mapped[TicketPriority] = ...
status: Mapped[TicketStatus] = mapped_column(..., server_default=TicketStatus.OPEN.value, ...)
redacted_transcript: Mapped[dict[str, Any]] = mapped_column(JSONB, ...)
version: Mapped[int] = mapped_column(..., server_default=text("1"))
```

- The docstring says `id == AgentState.ticket_id` — the ticket's PK is the same ID the orchestrator carries in its state.
- `conversation_id` is `unique=True`: exactly one ticket per conversation, and `ondelete="RESTRICT"` prevents deleting a conversation that spawned a ticket.
- `assigned_engineer_id` is the *current* owner pointer, nullable, `SET NULL` if the engineer is deleted.
- `redacted_transcript` is JSONB — the PII-scrubbed conversation snapshot handed to engineers.
- `version` is a simple optimistic-locking counter (increment on each write to detect concurrent edits).

Its relationships fan out to all five satellite tables, each with `cascade="all, delete-orphan"` (delete a ticket and its events, attachments, notes, assignments, and escalations all go with it).

`TicketEvent` is an append-only activity log:

```python
class TicketEvent(Base, CreatedAtMixin):
    event_type: Mapped[TicketEventType] = mapped_column(TICKET_EVENT_TYPE_ENUM, ..., index=True)
    from_status: Mapped[str | None] = ...
    to_status: Mapped[str | None] = ...
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, ...)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), ...)
```

Every state transition or comment writes one immutable row here. `from_status`/`to_status` capture transitions; `payload` holds extra detail; `actor_user_id` is nullable/`SET NULL` because a system/agent actor may have no user (or the user is later deleted).

`TicketAttachment` is a join table between tickets and files:

```python
class TicketAttachment(Base, CreatedAtMixin):
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), ...)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), ...)
    kind: Mapped[AttachmentKind] = mapped_column(ATTACHMENT_KIND_ENUM, ...)
```

`kind` classifies the attachment (screenshot/log/document/other). Both FKs cascade.

`EngineerNote` is internal commentary:

```python
class EngineerNote(Base, TimestampMixin, SoftDeleteMixin):
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), ...)
    visibility: Mapped[NoteVisibility] = mapped_column(..., server_default=NoteVisibility.INTERNAL.value)
    is_pinned: Mapped[bool] = mapped_column(..., server_default=text("false"))
```

Notes are editable and soft-deletable (hence `TimestampMixin` + `SoftDeleteMixin`, unlike the append-only events). `visibility` defaults to `internal`; `author_user_id` uses `RESTRICT` so you can't delete an engineer who authored notes.

`TicketAssignment` is the assignment history ledger:

```python
class TicketAssignment(Base, CreatedAtMixin):
    __table_args__ = (
        Index("uq_ticket_assignments_current", "ticket_id", unique=True,
              postgresql_where=text("is_current")),
    )
    is_current: Mapped[bool] = mapped_column(..., server_default=text("true"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(..., server_default=sa.func.now(), ...)
    unassigned_at: Mapped[datetime | None] = ...
```

Same partial-unique-index pattern as `ConversationSummary`: many historical assignment rows, but only **one** `is_current` per ticket. The docstring clarifies the division of labor — `tickets.assigned_engineer_id` is the fast "current pointer," while this table is the full audit trail (who assigned whom, why via `assignment_reason`, and when). `assigned_to_user_id` uses `RESTRICT`; `assigned_by_user_id` uses `SET NULL`.

`Escalation` records when a ticket/conversation is escalated:

```python
class Escalation(Base, CreatedAtMixin):
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, ...)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), ...)
    escalation_type: Mapped[EscalationType] = ...
    triggered_by: Mapped[EscalationTrigger] = ...
    reason_code: Mapped[str] = mapped_column(..., index=True)
```

Notice `ticket_id` is **nullable** but `conversation_id` is **not**: an escalation can happen (e.g. AI-to-human) *before* a ticket even exists, so it always ties to a conversation but only optionally to a ticket. `escalation_type` and `triggered_by` use their enums to record what kind of escalation and what caused it (confidence gate, retrieval gate, engineer, system).

**Connects to:** `Ticket` links to `conversations`, `users` (creator + assignee), and `category_registry`. Its five children all `back_populate` to `Ticket`. `TicketAttachment → files`. Downstream, `kb_documents.origin_ticket_id`, `feedback.ticket_id`, `notifications.ticket_id`, `learning_events.source_ticket_id`, and `analytics_events.ticket_id` all reference tickets.

---

### `knowledge.py` — knowledge base and ingestion

**Purpose:** The RAG (retrieval-augmented generation) knowledge base: `KbDocument` (a logical source doc), `KbChunk` (searchable text pieces, holding the full-text-search column), `KbDocumentVersion` (version history), `EmbeddingsMetadata` (per-chunk vector provenance), `KbIngestionJob` (the async pipeline tracker), and `KbApproval` (the human review gate).

**How it works:**

First, an important import detail explained in a comment:

```python
from sqlalchemy import text as sa_text
```

> `sqlalchemy.text` is aliased to `sa_text` because `KbChunk` defines a column literally named `text`... which would otherwise shadow the imported name.

Since there's a column called `text`, importing `text` normally would collide inside the class body — so it's renamed `sa_text` everywhere in this file.

```python
class KbDocument(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __table_args__ = (Index("ix_kb_documents_checksum", "checksum"),)
    source_type: Mapped[SourceType] = ...
    origin_ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), ...)
    category: Mapped[str] = mapped_column(ForeignKey("category_registry.category_key", ondelete="RESTRICT"), ...)
    retrieval_namespace: Mapped[str] = ...
    doc_status: Mapped[DocStatus] = mapped_column(..., server_default=DocStatus.DRAFT.value, ...)
    version: Mapped[int] = ...
    checksum: Mapped[str] = ...
```

- `id == AgentState.kb_doc_id` (docstring) — again the PK is shared with the orchestrator's state.
- `origin_ticket_id` links a KB article back to the ticket that inspired it (the "learn from resolutions" loop), nulled if the ticket is deleted.
- `retrieval_namespace` partitions the vector search space (e.g. per category), so retrieval only searches relevant docs.
- `doc_status` follows a lifecycle (draft → pending_review → published → …) defaulting to `draft`.
- `checksum` (indexed) detects duplicate/unchanged content.
- Four relationships: `chunks`, `versions`, `approvals` (all cascade), and `ingestion_jobs` (no cascade — jobs survive doc deletion for audit).

`KbChunk` is the heart of full-text search:

```python
class KbChunk(Base, TenantMixin, TimestampMixin):
    __table_args__ = (
        Index("ix_kb_chunks_retrieval_filter", "org_id", "retrieval_namespace", "doc_status", "last_verified_at"),
        Index("gin_kb_chunks_text_fts", "text_fts", postgresql_using="gin"),
    )
    category_key: Mapped[str] = mapped_column(ForeignKey("category_registry.category_key", ondelete="RESTRICT"), ...)
    text: Mapped[str] = mapped_column(nullable=False)
    text_fts: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text, ''))", persisted=True),
        nullable=True,
    )
    embedding_model_id: Mapped[str] = ...
```

Two things deserve close attention:

1. **The `Computed` `tsvector` column.** This is the key full-text-search mechanism. `text_fts` has type `TSVECTOR` (PostgreSQL's full-text search type — a preprocessed, tokenized form of text). `Computed(...)` makes it a **generated column**: you never write to it directly. The database computes it from the expression `to_tsvector('english', coalesce(text, ''))` — meaning "take the `text` column (or empty string if null), and produce an English-language search vector." `persisted=True` means the result is physically stored (a `STORED` generated column), so it's indexable. The GIN index `gin_kb_chunks_text_fts` on it makes keyword search extremely fast. `coalesce(text, '')` guards against `text` being null. So: you insert plain `text`, and PostgreSQL automatically maintains a searchable index vector — no application code needed.

2. **The naming divergence comment.** The FK here is called `category_key`, whereas `conversations`/`tickets`/`kb_documents` call the same FK `category`. The comment flags this is intentional, carried over from the approved database design — a heads-up so a developer doesn't "fix" it and break migrations.

The retrieval filter index `(org_id, retrieval_namespace, doc_status, last_verified_at)` mirrors exactly how retrieval queries filter chunks: tenant, namespace, only published, freshest first.

`KbDocumentVersion` snapshots history:

```python
class KbDocumentVersion(Base, CreatedAtMixin):
    __table_args__ = (
        UniqueConstraint("doc_id", "version", name="uq_kb_document_versions_doc_version"),
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, ...)
```

Immutable (`CreatedAtMixin` only). The `(doc_id, version)` unique constraint means each version number appears once per document. `snapshot` stores the full JSONB state of that version for rollback.

`EmbeddingsMetadata` tracks vector provenance:

```python
class EmbeddingsMetadata(Base, CreatedAtMixin):
    __table_args__ = (
        UniqueConstraint("chunk_id", "embedding_model_id", name="uq_embeddings_metadata_chunk_model"),
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_chunks.id", ondelete="CASCADE"), ..., unique=True, ...)
    vector_store: Mapped[VectorStore] = mapped_column(..., server_default=VectorStore.CHROMADB.value)
    is_stale: Mapped[bool] = mapped_column(..., server_default=sa_text("false"), index=True)
```

This is the bridge between a SQL chunk row and the actual vector living in ChromaDB (the external vector database). `vector_id`/`collection_name` locate the vector; `model_dim`/`embedding_model_id` record which model produced it; `is_stale` (indexed) flags chunks needing re-embedding after a model upgrade. Note `chunk_id` is itself `unique=True` (one metadata row per chunk), which is why the relationship on `KbChunk` uses `uselist=False` (a one-to-one). The unique constraint `(chunk_id, embedding_model_id)` guards against dup rows per model.

`KbIngestionJob` tracks the async pipeline:

```python
class KbIngestionJob(Base, TenantMixin, CreatedAtMixin):
    doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kb_documents.id", ondelete="SET NULL"), ...)
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"), ...)
    trigger: Mapped[IngestionTrigger] = ...
    status: Mapped[IngestionStatus] = mapped_column(..., server_default=IngestionStatus.QUEUED.value, ...)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, ...)
```

The docstring names the pipeline: "parse → chunk → embed → upsert." `status` walks through `IngestionStatus` (queued/parsing/chunking/embedding/upserting/completed/failed). Both FKs are nullable `SET NULL` so the job record survives even if its doc/file is removed. `started_at`/`finished_at`/`error` capture the run outcome.

`KbApproval` is the human review gate:

```python
class KbApproval(Base, CreatedAtMixin):
    decision: Mapped[ApprovalDecision] = mapped_column(..., server_default=ApprovalDecision.PENDING.value, ...)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), ...)
```

The docstring says it flips a doc from `pending_review` to `published`. Decision defaults to `pending`; `reviewer_id` is nullable (unassigned reviews).

**Connects to:** `KbDocument` links to `users`, `tickets` (origin), and `category_registry`. `KbChunk`, `KbDocumentVersion`, `EmbeddingsMetadata`, and `KbApproval` all hang off `KbDocument`; `EmbeddingsMetadata` also points at `KbChunk`. `KbIngestionJob` references `kb_documents` and `files`. Downstream, `feedback.relevance_signals` and `learning_events` reference `kb_documents`/`kb_chunks`/`kb_ingestion_jobs`/`kb_approvals`.

---

### `docsearch.py` — document-intelligence ("ask your files")

**Purpose:** A **separate**, user-facing document workspace: `UploadedDocument` (a file or URL a user attaches) and `UploadedChunk` (searchable passages). The docstring stresses it is "kept separate from the helpdesk knowledge base" — this is personal, per-user "ask your files," not the shared KB.

**How it works:**

```python
class UploadedDocument(Base, TenantMixin, CreatedAtMixin):
    __table_args__ = (Index("ix_uploaded_documents_user", "org_id", "user_id"),)
    source_type: Mapped[str] = mapped_column(nullable=False)  # pdf | docx | text | excel | url
    sheet: Mapped[str | None] = ...       # chosen Excel tab
    chunk_count: Mapped[int] = mapped_column(..., server_default=sa_text("0"))
```

Notice `source_type` here is a **plain string** (with an inline comment listing allowed values), *not* a native enum — a lighter-weight choice for this secondary feature. `sheet` records which Excel tab was ingested; `chunk_count` is a denormalized counter. The `(org_id, user_id)` index scopes queries to "my uploads."

```python
class UploadedChunk(Base, TenantMixin, CreatedAtMixin):
    __table_args__ = (
        Index("gin_uploaded_chunks_fts", "text_fts", postgresql_using="gin"),
        Index("ix_uploaded_chunks_owner", "org_id", "user_id"),
    )
    location: Mapped[str] = ...   # "Page 3", "Sheet L2 · Row 240", "Section 2"
    text: Mapped[str] = ...        # verbatim passage
    text_fts: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text, ''))", persisted=True),
        nullable=True,
    )
```

This mirrors `KbChunk`'s full-text-search design exactly: the same `Computed` `TSVECTOR` generated column (`to_tsvector('english', coalesce(text, ''))`, `persisted=True`) plus a GIN index (`gin_uploaded_chunks_fts`) for fast keyword search. The distinctive field is `location`, a human-readable citation label (the comment shows examples like `"Page 3"` or `"Sheet L2 · Row 240"`) so answers can point users to exactly where a passage came from. Interestingly this feature relies purely on PostgreSQL full-text search — no vector store — unlike the KB.

**Connects to:** `UploadedDocument.user_id → users`; `UploadedChunk` belongs to `UploadedDocument` (cascade delete) and also directly to `users`. Deliberately **not** connected to the `kb_*` tables.

---

### `feedback.py` — feedback and learning signals

**Purpose:** Captures the learning loop: `Feedback` (user thumbs/reopen/comments), `RelevanceSignal` (aggregated per-chunk retrieval signals), and `LearningEvent` (the audit trail of the automated feedback-learner).

**How it works:**

```python
class Feedback(Base, TenantMixin, CreatedAtMixin):
    __table_args__ = (
        Index("ix_feedback_unprocessed", "processed_at",
              postgresql_where=text("processed_at IS NULL")),
    )
    rating: Mapped[FeedbackRating] = mapped_column(FEEDBACK_RATING_ENUM, ..., index=True)
    feedback_handle: Mapped[str] = mapped_column(..., index=True)
    processed_at: Mapped[datetime | None] = ...
```

The partial index `ix_feedback_unprocessed` only covers rows where `processed_at IS NULL` — a classic **work-queue index**: the background learner efficiently finds unprocessed feedback without scanning the whole table. `rating` uses the enum (up/down/reopen). `message_id` is `SET NULL` (feedback survives message deletion) while `user_id`/`conversation_id` cascade. `feedback_handle` is an indexed correlation key.

```python
class RelevanceSignal(Base):
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_id", name="uq_relevance_signals_doc_chunk"),
    )
    upvotes / downvotes / impressions / resolution_success: Mapped[int] = ...
    boost_factor: Mapped[float] = mapped_column(..., server_default=text("1.0"))
    is_quarantined: Mapped[bool] = ...
    updated_at: Mapped[datetime] = mapped_column(..., server_default=sa.func.now(), onupdate=sa.func.now(), ...)
```

Notice `RelevanceSignal` inherits **only `Base`** — no mixins, no `org_id`, no `created_at`. It's a pure aggregate/counter table. The docstring says it's "consumed by the reranker + retrieval_gate": it accumulates vote and impression counters per doc/chunk, computes a `boost_factor` to nudge search ranking, and flags bad content via `is_quarantined`. It defines its own `updated_at` with `onupdate` since it doesn't use `TimestampMixin`. The `(doc_id, chunk_id)` unique constraint means one signal row per chunk (with `chunk_id` nullable, allowing a doc-level aggregate row too).

```python
class LearningEvent(Base, TenantMixin, CreatedAtMixin):
    trigger: Mapped[LearningTrigger] = ...
    source_ticket_id / source_feedback_id / source_doc_id / resulting_doc_id
      / ingestion_job_id / approval_id: Mapped[uuid.UUID | None] = ... ForeignKey(..., ondelete="SET NULL")
    status: Mapped[LearningStatus] = mapped_column(..., index=True)
```

This is the audit ledger of the "feedback_learner loop." It stitches together the entire chain of a learning run — the triggering ticket/feedback, the source doc, the newly produced doc, the ingestion job, and the approval — via six nullable foreign keys, all `SET NULL` so the ledger entry survives even if any linked record is later deleted. `status` (indexed) tracks progress through `LearningStatus` (drafted → pending_approval → … → upserted).

**Connects to:** `Feedback → users, conversations, messages, tickets`. `RelevanceSignal → kb_documents, kb_chunks`. `LearningEvent → tickets, feedback, kb_documents (×2), kb_ingestion_jobs, kb_approvals`. This file is essentially the "glue" between the ticketing, feedback, and knowledge-base subsystems.

---

### `registry.py` — data-driven configuration

**Purpose:** The extensibility seam: `CategoryRegistry` (the category taxonomy that drives routing, intake, SLA, and thresholds — seeded with 8 rows) and `PromptTemplate` (a versioned prompt catalog for the agent nodes).

**How it works:**

```python
class CategoryRegistry(Base):
    __table_args__ = (
        Index("gin_category_registry_thresholds", "thresholds", postgresql_using="gin"),
    )
    category_key: Mapped[str] = mapped_column(primary_key=True)
    required_intake_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, ...)
    retrieval_namespace: Mapped[str] = ...
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, ...)
    tool_bindings: Mapped[dict[str, Any]] = mapped_column(JSONB, ...)
```

This is a special table for two reasons:
1. **String primary key.** Its PK is `category_key` (a `str`), not a UUID. This is why so many other tables (`conversations.category`, `tickets.category`, `kb_documents.category`, `kb_chunks.category_key`) foreign-key to `category_registry.category_key` — they reference the human-readable key directly.
2. **Data-driven behavior.** Rather than hard-coding category logic, the app reads it from rows here: `required_intake_fields` (what to ask the user), `retrieval_namespace` (which KB partition to search), `sla_tier`, `handoff_queue`, `thresholds` (confidence cutoffs, GIN-indexed for querying inside the JSON), and `tool_bindings` (which tools the agent may use). Adding a new support category becomes a data insert, not a code change — that's the "extensibility seam."

It inherits only `Base` (it's global config, not tenant-scoped).

```python
class PromptTemplate(Base, CreatedAtMixin):
    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_prompt_templates_key_version"),
        Index("uq_prompt_templates_active_key", "key", unique=True,
              postgresql_where=text("is_active")),
    )
    node_id: Mapped[str] = ...
    content: Mapped[str] = ...
    is_active: Mapped[bool] = mapped_column(..., server_default=text("true"), index=True)
```

`PromptTemplate` stores the actual prompt strings the agent nodes use, versioned for "A/B + rollback" (docstring). It combines two constraints:
- `UniqueConstraint("key", "version")` — each version of a prompt key exists once (full history retained).
- A **partial unique index** on `key WHERE is_active` — the same "only one active per key" pattern seen in `ConversationSummary` and `TicketAssignment`. Many versions may exist, but only one is live per key.

`variables` (JSONB) declares the template's placeholders; `model_tier` optionally pins which model runs it.

**Connects to:** `CategoryRegistry.category_key` is the target of category FKs across `conversation.py`, `ticket.py`, and `knowledge.py`. `PromptTemplate.created_by_user_id → users`. This is a "hub" that many domain tables point *into*.

---

### `checkpoint.py` — the LangGraph checkpointer catalog

**Purpose:** Documents (but does not manage) the `graph_checkpoints` table that the LangGraph Postgres checkpointer owns. The whole point of this file is a clean ownership boundary.

**How it works:**

The docstring is the key to understanding this file:

> the `graph_checkpoints` DDL is owned and created by the LangGraph Postgres checkpointer's own `setup()` routine ... NOT by the application's autogenerated migrations.

To enforce that boundary in code, the model uses a **completely separate declarative base**:

```python
class CheckpointBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

class GraphCheckpoint(CheckpointBase):
    __tablename__ = "graph_checkpoints"
    thread_id: Mapped[str] = mapped_column(primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(primary_key=True, server_default="")
    checkpoint_id: Mapped[str] = mapped_column(primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = ...
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, ...)
```

Because `GraphCheckpoint` lives on `CheckpointBase` (not the app's `Base`), it never enters `Base.metadata`. That means Alembic autogenerate and `create_all` completely ignore it — the app won't try to create or migrate a table that a third-party library manages. It reuses `NAMING_CONVENTION` for consistency but is otherwise isolated.

Two implementation details worth noting:
- **Composite primary key.** Three columns — `thread_id`, `checkpoint_ns`, `checkpoint_id` — together form the PK. Recall `thread_id` equals the conversation's `id`; this is how the graph state is keyed back to a conversation. `checkpoint_ns` defaults to an empty string.
- **The `metadata_` name trick.** The column is declared as `metadata_: Mapped[...] = mapped_column("metadata", JSONB, ...)`. The Python attribute is `metadata_` (trailing underscore) because `metadata` is a reserved name on SQLAlchemy declarative classes — but the first positional argument `"metadata"` sets the **actual database column name** to `metadata`. So Python sees `metadata_`, the DB sees `metadata`.

The model exists "purely to document the shape and to allow read-only ORM access if ever required" — it's a catalog entry, not an app-managed table.

**Connects to:** Deliberately **isolated** from `Base.metadata`. It is imported in `__init__.py` for convenience but stays out of application migrations. Conceptually it links to `conversations` because `thread_id == conversation.id`, but there is no enforced foreign key (the library owns the table).

---

### How the pieces fit together

Reading the files as a whole, a few consistent design patterns emerge that are worth internalizing:

- **One base, shared mixins, shared enums.** `base.py` centralizes conventions so the other ~11 files stay short and consistent. The native enum objects are defined once and imported, so each PostgreSQL enum type is created exactly once.
- **Tenancy everywhere.** Most tables carry `org_id` via `TenantMixin`, and composite indexes lead with `org_id`.
- **Append-only vs. mutable.** Logs and events use `CreatedAtMixin` (write-once); editable entities use `TimestampMixin`; user content adds `SoftDeleteMixin`.
- **Partial unique indexes for "exactly one current."** `ConversationSummary`, `TicketAssignment`, and `PromptTemplate` all use `unique=True` + `postgresql_where=...` to keep full history while enforcing a single active/current row.
- **JSONB + GIN for flexible, queryable data**, and **`Computed` `TSVECTOR` + GIN for full-text search** (`KbChunk.text_fts` and `UploadedChunk.text_fts`), where the database — not the app — keeps the search vector in sync.
- **`category_registry.category_key` as a data-driven hub**, referenced by conversations, tickets, and knowledge docs, making the taxonomy configurable via data rather than code.
- **The checkpointer table is intentionally quarantined** on its own metadata so a third-party library, not the app's migrations, owns it.

Relevant files (all absolute):
- `backend\app\models\base.py`
- `backend\app\models\__init__.py`
- `backend\app\models\organization.py`
- `backend\app\models\user.py`
- `backend\app\models\conversation.py`
- `backend\app\models\ticket.py`
- `backend\app\models\knowledge.py`
- `backend\app\models\docsearch.py`
- `backend\app\models\feedback.py`
- `backend\app\models\ops.py`
- `backend\app\models\registry.py`
- `backend\app\models\checkpoint.py`

*(Enum definitions referenced by every model live in `backend\app\core\constants.py`.)*

---

I now have everything needed. Here is the walkthrough.

## Backend — DB Session & Repositories

This section covers the persistence bootstrap (`backend/app/db`) and the data-access layer (`backend/app/repositories`). Together they answer two questions: *"How does the app open a database connection?"* and *"How does the rest of the code read and write rows without ever writing raw SQL by hand?"*

A quick mental model before we dive in:

- **`db/session.py`** owns the async connection pool and hands out one short-lived database session per web request.
- **`db/base.py`** is a tiny "table registry" that Alembic (the migration tool) looks at to know what the schema *should* be.
- **`db/migrations/`** contains the versioned SQL history that builds the real database.
- **`repositories/`** wraps every table in small Python classes so services call `repo.get(...)` instead of writing `SELECT ...`.

---

### `backend/app/db/__init__.py`

**Purpose:** Marks `app/db` as a Python package and documents its role.

**How it works:** It contains only a one-line docstring: `"""Persistence bootstrap package (async engine/session + Alembic surface)."""`. There is no code — its only job is to make `app.db` importable so that `app.db.session` and `app.db.base` can be reached as submodules.

**Connects to:** Everything under `app/db` (`session.py`, `base.py`, `migrations/`).

---

### `backend/app/db/session.py`

**Purpose:** Creates the single async SQLAlchemy engine (the connection pool) for the whole app, a factory that produces database sessions, and the FastAPI dependency that gives each request its own session with automatic commit/rollback.

**How it works:**

The module first pulls in configuration and builds the engine exactly once, at import time:

```python
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
```

Line by line:
- `sqlalchemy_async_dsn` is a computed property on the settings object. It returns a connection string like `postgresql+asyncpg://user:password@host:port/dbname`. The `+asyncpg` part tells SQLAlchemy to use the async Postgres driver, which is what lets the app do non-blocking `await` database calls.
- `echo=DB_ECHO` — when true, SQLAlchemy logs every SQL statement (handy in development, off by default).
- `pool_pre_ping=True` — before handing out a pooled connection, SQLAlchemy runs a tiny check to make sure it is still alive. This avoids the classic "server closed the connection" error after an idle period.
- `pool_size` / `max_overflow` / `pool_timeout` — how many connections stay open (20), how many extra it may temporarily open under load (10), and how many seconds a caller waits for a free connection before erroring (30). These come straight from config defaults.
- Because `engine` is a **module-level variable**, it is created only once per process. This is deliberate: opening a fresh pool per request would be slow and would exhaust the database. Import the module anywhere and you get the same shared engine (a singleton).

Next it builds the session factory:

```python
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)
```

- `bind=engine` ties every session it creates to the shared pool above.
- `expire_on_commit=False` is important: by default SQLAlchemy "expires" objects after commit, meaning the next time you read an attribute it re-queries the database. That re-query would fail in async code because it happens lazily outside an `await`. Turning it off means the objects you already loaded stay usable (with their loaded values) after commit.
- `autoflush=False` — SQLAlchemy will not automatically flush pending changes before every query; the repositories flush explicitly when they need to (you will see `await self.session.flush()` a lot below). This gives predictable control over when SQL is emitted.
- `autocommit=False` — the app controls transaction boundaries itself (see the dependency next).

The request-scoped dependency:

```python
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

This is a FastAPI dependency. FastAPI calls it for each incoming request, `yield`s the `session` into the route handler, and the code *after* the `yield` runs once the handler finishes:
- If the handler returns normally, `await session.commit()` saves all the changes made during that request.
- If anything raised, `await session.rollback()` undoes every pending change, then `raise` re-throws so the error still bubbles up as an HTTP error.
- The `async with SessionFactory() as session:` block guarantees the session is closed (and its connection returned to the pool) no matter what.

The pattern is "one transaction per HTTP request." Repositories just add/flush rows; the actual `COMMIT` happens here at the edge. (The docstring notes services may own finer-grained transaction control where needed.)

Two lifecycle/health helpers round it out:

```python
async def dispose_engine() -> None:
    await engine.dispose()
```

`dispose_engine` closes every pooled connection; the app calls it on shutdown so it doesn't leave dangling connections on the database.

```python
async def check_database() -> bool:
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
```

`check_database` is a readiness probe: it opens a connection and runs the cheapest possible query, `SELECT 1`. If that works it returns `True`; if anything fails it swallows the error and returns `False`. The bare `except` is intentional (see the `# noqa` comment) — a health check must never itself crash the process.

**Connects to:**
- `app.core.config.get_settings()` for the DSN and pool tuning.
- `app.models.base.Base` indirectly — the ORM models the repositories query are all bound to this engine's sessions.
- FastAPI routes/services depend on `get_session` (typically via `Depends(get_session)`); every repository below receives one of these `AsyncSession` objects in its constructor.
- The app's startup/shutdown wiring calls `dispose_engine` and `check_database`.

---

### `backend/app/db/base.py`

**Purpose:** Provides the single `metadata` object that Alembic points at when it compares your ORM models to the live database schema (this is what `--autogenerate` diffs against).

**How it works:**

```python
from app.models import *  # noqa: F401,F403  (register all tables on Base.metadata)
from app.models.base import Base

target_metadata = Base.metadata
```

- The star import of `app.models` pulls in every model module. In SQLAlchemy, simply *defining* a model class (a subclass of `Base`) registers its table on `Base.metadata`. So this import is the step that "wakes up" all 36 tables and attaches them to the shared metadata. The `# noqa` comment silences linter warnings about the unused wildcard import — the import has a side effect (registration), which is the whole point.
- `target_metadata = Base.metadata` exposes that fully-populated metadata under the name Alembic expects.

The docstring makes one subtle but important point: the LangGraph checkpointer table `graph_checkpoints` lives on a *separate* metadata (`CheckpointBase`), deliberately **not** on `Base.metadata`. That keeps it out of Alembic autogenerate, because that table is managed by LangGraph's own setup routine, not by the app's migrations.

**Connects to:**
- `app.models.base.Base` — the declarative base every ORM model inherits from.
- `app.models` package — imported for its registration side effect.
- `db/migrations/env.py` and the initial migration `0001_initial_schema.py`, both of which import `target_metadata` from here.

---

### `backend/app/db/migrations/env.py`

**Purpose:** The Alembic "environment" script. It configures how migrations connect and run — using a **synchronous** psycopg engine (Alembic is not async) and the app's `target_metadata` — and supports both offline (SQL-to-stdout) and online (live database) modes.

**How it works:**

First it makes sure the `app` package is importable, because Alembic may be invoked from a different working directory:

```python
_BACKEND_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
```

It walks three directories up from this file (`migrations` → `db` → `app` → backend root) and prepends it to `sys.path`.

Then it wires config:

```python
config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_sync_dsn)
```

- Note it uses `sqlalchemy_sync_dsn` (the `postgresql+psycopg://...` string), **not** the async one — Alembic runs synchronously.
- The URL is injected at runtime from settings rather than being hardcoded in `alembic.ini`. The comment says why: "never store credentials in alembic.ini."

`run_migrations_offline()` emits SQL text without ever touching a real database (useful for reviewing or running SQL by hand). It configures the context with `literal_binds=True` (so parameter values are written inline into the SQL) and turns on `compare_type=True` / `compare_server_default=True` so autogenerate notices column-type and default changes.

`run_migrations_online()` is the normal path:

```python
connectable = engine_from_config(
    config.get_section(config.config_ini_section, {}),
    prefix="sqlalchemy.",
    poolclass=pool.NullPool,
)
with connectable.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()
```

- It builds a real sync engine. `poolclass=pool.NullPool` means "don't pool" — a migration runs once and exits, so pooling adds no value.
- It opens one connection, binds Alembic's context to it and to `target_metadata`, opens a transaction, and runs the pending migration functions.

The final dispatch chooses the mode:

```python
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Connects to:**
- `app.core.config.get_settings().sqlalchemy_sync_dsn` for the migration connection.
- `app.db.base.target_metadata` — the schema-of-record it diffs against.
- The `versions/` migration scripts, whose `upgrade()`/`downgrade()` functions it executes.

---

### `backend/app/db/migrations/script.py.mako`

**Purpose:** The template Alembic uses when generating a new migration file (`alembic revision`). It defines the skeleton every migration shares.

**How it works:** It is a Mako template — the `${...}` placeholders are filled in by Alembic at generation time:
- `${up_revision}` / `${down_revision}` become the `revision` and `down_revision` identifiers that chain migrations into an ordered history.
- `${imports}`, `${upgrades}`, and `${downgrades}` are where autogenerate injects the detected schema operations.
- The generated file always defines `upgrade()` (apply the change) and `downgrade()` (reverse it), plus `branch_labels` and `depends_on` for advanced branching (usually `None`).

The header `from __future__ import annotations` and typed identifiers (`revision: str`, `down_revision: str | None`) mean every generated migration is consistently typed.

**Connects to:** Alembic tooling only; it shapes the files in `versions/`.

---

### `backend/app/db/migrations/versions/0001_initial_schema.py`

**Purpose:** The very first migration. It creates the entire application schema — all 36 tables, enum types, generated columns, and indexes — in one shot, directly from the ORM metadata.

**How it works:**

Identity and ordering:
```python
revision: str = "0001_initial_schema"
down_revision: str | None = None
```
`down_revision = None` marks this as the root of the migration chain (nothing comes before it).

The `upgrade()`:
```python
def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    target_metadata.create_all(bind=bind)
```
- `op.get_bind()` gets the live connection Alembic is running on.
- Two Postgres extensions are enabled first: `citext` (case-insensitive text — used for `users.email`, so `Foo@x.com` and `foo@x.com` match) and `pgcrypto` (provides `gen_random_uuid()` for UUID primary keys on older Postgres).
- `target_metadata.create_all(bind=bind)` is the clever part: instead of hand-writing 36 `CREATE TABLE` statements, it asks SQLAlchemy to emit them all from the models. SQLAlchemy resolves foreign-key ordering automatically and generates the native enum types, the generated `tsvector` columns (like `kb_chunks.text_fts`), and every declared GIN/BRIN/partial index. This guarantees the initial database exactly matches the ORM — the models are the single source of truth.

The `downgrade()` is the mirror image: `target_metadata.drop_all(bind=bind)` drops everything.

The docstring flags that `graph_checkpoints` is intentionally *not* created here — it comes in the next revision.

**Connects to:**
- `app.db.base.target_metadata` — the source it builds from.
- Every model in `app.models`.
- `0002_checkpointer_setup.py`, which lists this as its `down_revision`.

---

### `backend/app/db/migrations/versions/0002_checkpointer_setup.py`

**Purpose:** Creates the LangGraph Postgres checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) by delegating to LangGraph's own setup routine, pinned inside a dedicated migration.

**How it works:**

It chains after the initial schema:
```python
revision: str = "0002_checkpointer_setup"
down_revision: str | None = "0001_initial_schema"
```

A small helper converts the SQLAlchemy DSN into a plain libpq DSN:
```python
def _libpq_dsn() -> str:
    return get_settings().sqlalchemy_sync_dsn.replace(
        "postgresql+psycopg://", "postgresql://"
    )
```
LangGraph's saver opens its own raw libpq connection and does not understand SQLAlchemy's `+psycopg` driver tag, so it is stripped out.

The `upgrade()`:
```python
try:
    from langgraph.checkpoint.postgres import PostgresSaver
except ImportError as exc:
    raise RuntimeError("langgraph-checkpoint-postgres must be installed ...") from exc

with PostgresSaver.from_conn_string(_libpq_dsn()) as checkpointer:
    checkpointer.setup()
```
- The import is guarded so that if the dependency is missing you get a clear, actionable error instead of a cryptic `ImportError`.
- `checkpointer.setup()` creates whatever tables that version of LangGraph needs. Letting LangGraph own its own schema (rather than hand-modeling those tables) means future LangGraph upgrades won't drift from what the app expects. This is exactly why these tables were kept off `Base.metadata`.

The `downgrade()` drops the four known tables with `CASCADE`:
```python
for table in _CHECKPOINTER_TABLES:
    op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
```
`IF EXISTS` makes it safe to run even if a table is missing.

**Connects to:**
- `app.core.config` for the DSN.
- `langgraph.checkpoint.postgres.PostgresSaver` (an external dependency).
- `0001_initial_schema.py` (its parent) and `0003_seed_roles_and_categories.py` (its child).

---

### `backend/app/db/migrations/versions/0003_seed_roles_and_categories.py`

**Purpose:** A *data* migration (as opposed to a schema migration). It seeds the canonical RBAC roles and the 8 rows of `category_registry`, idempotently, so every environment (dev, staging, prod) converges to the same baseline data.

**How it works:**

It chains after the checkpointer migration (`down_revision = "0002_checkpointer_setup"`).

Two constant lists hold the seed data. `_ROLES` is four `(key, display_name)` tuples: `end_user`, `support_engineer`, `admin`, `sme_reviewer`. `_CATEGORIES` is eight dictionaries, one per helpdesk category (login, password reset, VPN, payment, software install, application error, email, hardware). Each carries the intake fields, an SLA tier, a handoff queue, and per-category **thresholds** used by the AI pipeline (retrieval/deliver confidence cutoffs, minimum grounding, and a retry budget). Notice `payment` is stricter than the rest — higher thresholds and `"retry_budget": 0` — reflecting that billing mistakes are costly (the docstring calls this out).

The `upgrade()` uses **upserts** so the migration is safe to run more than once:
```python
role_stmt = sa.text(
    "INSERT INTO roles (key, display_name) VALUES (:key, :display_name) "
    "ON CONFLICT (key) DO NOTHING"
)
for key, display_name in _ROLES:
    op.execute(role_stmt.bindparams(key=key, display_name=display_name))
```
`ON CONFLICT (key) DO NOTHING` means "if a role with this key already exists, skip it" — that is what makes it idempotent (re-running won't create duplicates or error). Values are passed via `.bindparams(...)`, i.e. parameterized, not string-concatenated, so there is no SQL-injection surface.

Categories follow the same pattern, with JSON columns handled by casting text to `jsonb`:
```python
VALUES (
    :key, :display_name, CAST(:intake AS jsonb), :namespace,
    :sla_tier, :queue, CAST(:thresholds AS jsonb), CAST(:tool_bindings AS jsonb), true
)
ON CONFLICT (category_key) DO NOTHING
```
The dicts are serialized with `json.dumps(...)` before binding. The `retrieval_namespace` is set equal to the category key, and `tool_bindings` starts as an empty object `{}`.

The `downgrade()` deletes exactly the seeded rows using an *expanding* bind parameter so a Python list becomes a proper SQL `IN (...)` list:
```python
op.execute(
    sa.text("DELETE FROM category_registry WHERE category_key IN :keys").bindparams(
        sa.bindparam("keys", value=tuple(category_keys), expanding=True)
    )
)
```
`expanding=True` is the correct way to bind a variable-length `IN` list in SQLAlchemy. Roles are removed the same way.

**Connects to:**
- The `roles` and `category_registry` tables created in `0001`.
- The AI/service layer that reads `category_registry.thresholds` at runtime to decide when to answer, clarify, or hand off.

---

## Repositories

The repository layer is the *only* layer allowed to touch the ORM and the session. It holds **no business rules** — that is the service layer's job — just data access. A key design rule (visible throughout): every tenant-scoped method takes an explicit `org_id` so a query can never accidentally return another organization's rows.

### `backend/app/repositories/__init__.py`

**Purpose:** Re-exports all repository classes from one place so callers can write `from app.repositories import TicketRepository` instead of reaching into each module.

**How it works:** It imports each repository (`AnalyticsRepository`, `AuditRepository`, `BaseRepository`, `ConversationRepository`, `FeedbackRepository`, `KnowledgeRepository`, `MemoryRepository`, `NotificationRepository`, `TicketRepository`, `UserRepository`) and lists them in `__all__`. Note `UserSessionRepository` is defined in `user_repo.py` but is not re-exported here — callers that need it import it directly from `app.repositories.user_repo`.

**Connects to:** Every repository module in the folder; consumed by the services layer.

---

### `backend/app/repositories/base.py`

**Purpose:** A generic async CRUD base class that every concrete repository inherits from. It provides read helpers, write helpers, soft-delete, and a set of tenant-scoped (`*_for_org`) helpers, so the specific repositories only add table-specific queries.

**How it works:**

It is generic over the model type:
```python
ModelType = TypeVar("ModelType", bound=Base)

class BaseRepository(Generic[ModelType]):
    model: type[ModelType]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
```
- `ModelType` is bound to `Base`, so a repository can only be parameterized with a real ORM model. Subclasses set `model = SomeModel` as a class attribute, and the generic typing (`BaseRepository[Ticket]`) gives editors/type-checkers precise return types.
- Every repository is constructed with an `AsyncSession` — the same request-scoped session `get_session` yields. This is dependency injection: the repository never creates or commits a session, it just uses the one handed to it.

**Reads:**
- `get(entity_id)` — `session.get(...)` is a primary-key lookup that also checks the session's identity map first (cheap if already loaded).
- `get_by(**filters)` — `select(...).filter_by(**filters).limit(1)` then `scalar_one_or_none()`, which returns the row or `None` (and raises if somehow more than one matched, though `limit(1)` prevents that).
- `exists(**filters)` and `count(**filters)` — both run `select(func.count()).select_from(self.model).filter_by(**filters)`. `exists` casts the count to `bool`; `count` casts to `int`.
- `list(...)`:
  ```python
  stmt = select(self.model).filter_by(**filters)
  stmt = stmt.order_by(self._default_order() if order_by is None else order_by)
  stmt = stmt.limit(limit).offset(offset)
  ```
  Keyword-only `limit`/`offset`/`order_by` give standard pagination. If no `order_by` is passed it falls back to `_default_order()` (explained below). `result.scalars().all()` returns model instances rather than raw rows.

**Writes** (note none of these commit — they only `flush`):
- `create(**values)` — instantiates the model, `session.add(instance)`, then `await self.session.flush()`. Flushing sends the `INSERT` to the database *within the current transaction* so server-generated fields (like the UUID id) are populated, but the enclosing request transaction is what ultimately commits.
- `add(instance)` — same as create but for an object you already built.
- `update(instance, **values)` — loops `setattr(instance, key, value)` for each field, then flushes.
- `delete(instance)` — hard delete via `session.delete(...)` + flush.
- `soft_delete(instance)`:
  ```python
  if not hasattr(instance, "deleted_at"):
      raise AttributeError(f"{self.model.__name__} does not support soft delete ...")
  instance.deleted_at = datetime.now(timezone.utc)
  ```
  Instead of removing the row, it stamps `deleted_at` with a timezone-aware UTC time. It guards first so calling it on a table without a `deleted_at` column fails loudly rather than silently doing nothing.

**Tenant-scoped helpers** — the safety-critical part:
- `get_for_org(entity_id, org_id)` filters on both `id` and `org_id`, so you can never fetch a record by id that belongs to another tenant.
- `list_for_org(org_id, ...)` and `count_for_org(org_id, ...)` add `.where(self.model.org_id == org_id)` before any other filters.
- Each of these first calls `self._require_tenant()`:
  ```python
  def _require_tenant(self) -> None:
      if not hasattr(self.model, "org_id"):
          raise AttributeError(f"{self.model.__name__} is not tenant-scoped (no org_id column).")
  ```
  So if someone tries a tenant-scoped query on a model that has no `org_id`, it errors immediately instead of returning cross-tenant data. The `# type: ignore[attr-defined]` comments on `self.model.id` / `self.model.org_id` exist because the generic `ModelType` doesn't statically guarantee those columns — the runtime guard is what actually enforces it.

**Default ordering:**
```python
def _default_order(self) -> Any:
    for column_name in ("created_at", "occurred_at", "updated_at", "id"):
        column = getattr(self.model, column_name, None)
        if column is not None:
            return column.desc()
    return None
```
It picks the first timestamp-like column the model actually has and orders newest-first. This gives every `list` a sensible default (most-recent-first) without each subclass specifying it.

**Connects to:**
- `app.models.base.Base` (the `TypeVar` bound).
- `AsyncSession` from `db/session.py`.
- Every concrete repository subclasses it.

---

### `backend/app/repositories/user_repo.py`

**Purpose:** Data access for `User` and `UserSession`. Handles case-insensitive email lookups, eager-loading the user's role, and login-session lifecycle (create/find/revoke refresh-token sessions).

**How it works:**

`UserRepository(BaseRepository[User])`:
- `get_by_email(org_id, email)`:
  ```python
  select(User)
    .where(User.org_id == org_id, User.email == email)
    .options(selectinload(User.role))
    .limit(1)
  ```
  Because `User.email` is a `CITEXT` column (set up by the citext extension in migration 0001), the `==` comparison is automatically case-insensitive at the database level — no `LOWER(...)` needed. `selectinload(User.role)` eagerly loads the related role in a second query, avoiding a lazy-load later (lazy loads are dangerous in async code). Returns the row or `None`.
- `get_active_by_email(...)` adds `User.is_active.is_(True)` and `User.deleted_at.is_(None)` — used for authentication, where you only want a live, non-deleted account.
- `get_with_role(user_id)` — fetch by id with the role eagerly loaded (used after you already know the user id, e.g. from a token).
- `email_exists(org_id, email)` — delegates to the base `exists(org_id=..., email=...)`.
- `mark_logged_in(user)` — stamps `user.last_login_at = datetime.now(timezone.utc)` then flushes.

`UserSessionRepository(BaseRepository[UserSession])` manages refresh-token sessions:
- `get_active_by_hash(token_hash)` — finds a session whose `refresh_token_hash` matches and whose `revoked_at IS NULL`. Note it looks up by **hash**, not the raw token — the plaintext refresh token is never stored.
- `list_active_for_user(user_id)` — all non-revoked sessions for a user.
- `revoke(session_row)` — sets `revoked_at` to now (logout for one device).
- `revoke_all_for_user(user_id)`:
  ```python
  rows = await self.list_active_for_user(user_id)
  now = datetime.now(timezone.utc)
  for row in rows:
      row.revoked_at = now
  await self.session.flush()
  return len(rows)
  ```
  Revokes every active session (logout everywhere / password change) and returns how many were revoked.

**Connects to:**
- `app.models.user.User` / `UserSession`.
- `sqlalchemy.orm.selectinload` for eager role loading.
- The auth service (login, token refresh, logout) — the primary consumer.

---

### `backend/app/repositories/conversation_repo.py`

**Purpose:** Data access for `Conversation` and its `Message` rows — listing a user's conversations, fetching one they own, appending messages with monotonically increasing turn numbers, and counting clarification turns.

**How it works:**

`ConversationRepository(BaseRepository[Conversation])`:
- `list_for_user(org_id, user_id, ...)` — filters by org + user + `deleted_at IS NULL`, ordered by `last_message_at DESC NULLS LAST` (most recently active conversations first; ones with no messages sink to the bottom).
- `get_for_user(conversation_id, user_id)` — an ownership-scoped fetch: it filters by both the id *and* the `user_id`, so a user can only load their own conversation.
- `touch_last_message(conversation, when=None)` — updates `last_message_at` to `when` (or now) and flushes. Called whenever a new message lands so the ordering above stays fresh.

Messages:
- `next_turn_id(conversation_id)`:
  ```python
  select(func.coalesce(func.max(Message.turn_id), 0)).where(
      Message.conversation_id == conversation_id
  )
  ...
  return int(result.scalar_one()) + 1
  ```
  Computes the next turn number: the max existing `turn_id` (or 0 if none, via `coalesce`) plus 1. This gives conversations a clean 1, 2, 3… sequence.
- `add_message(...)` — builds a `Message` with role, content, `trace_id` (for observability), and optional `citations`, `decision`, and `token_usage`, then adds + flushes.
- `count_assistant_clarifications(conversation_id)`:
  ```python
  select(func.count()).select_from(Message).where(
      Message.conversation_id == conversation_id,
      Message.role == MessageRole.ASSISTANT,
      Message.decision == Decision.CLARIFY,
  )
  ```
  Counts how many times the assistant has already asked the user to clarify. The AI pipeline uses this to cap clarification loops (so it doesn't ask forever and eventually hands off instead).
- `list_messages(conversation_id, ...)` — messages ordered by `turn_id ASC` (chronological), paginated.

**Connects to:**
- `app.models.conversation.Conversation` / `Message`.
- `app.core.constants.Decision` and `MessageRole` (enums).
- The chat/agent orchestration service, which reads history, appends turns, and checks the clarification count.

---

### `backend/app/repositories/memory_repo.py`

**Purpose:** Data access for conversation memory — durable per-user "facts" and versioned rolling conversation summaries.

**How it works:**

`MemoryRepository(BaseRepository[MemoryFact])`:

Durable facts (stable things learned about a user, e.g. their OS or department):
- `list_facts(user_id)` — all facts for a user.
- `get_fact(user_id, fact_key)` — one fact by its key (delegates to base `get_by`).
- `upsert_fact(...)`:
  ```python
  existing = await self.get_fact(user_id, fact_key)
  if existing is not None:
      existing.fact_value = fact_value
      existing.confidence = confidence
      if source_conversation_id is not None:
          existing.source_conversation_id = source_conversation_id
      await self.session.flush()
      return existing
  fact = MemoryFact(...)
  self.session.add(fact)
  await self.session.flush()
  return fact
  ```
  A read-then-write upsert done in Python: if the fact already exists, update its value/confidence in place; otherwise insert a new row. `source_conversation_id` records where the fact came from, only overwriting it when a new source is supplied.

Rolling summaries (compressed history so the model doesn't re-read every message):
- `get_current_summary(conversation_id)` — the one summary with `is_current = True`.
- `add_summary(...)`:
  ```python
  current = await self.get_current_summary(conversation_id)
  next_version = 1
  if current is not None:
      next_version = current.version + 1
      await self.session.execute(
          update(ConversationSummary)
          .where(..., ConversationSummary.is_current.is_(True))
          .values(is_current=False)
      )
  summary = ConversationSummary(..., version=next_version, is_current=True)
  ```
  This is a **versioned "current pointer"** pattern: before inserting the new summary, it flips the old current one to `is_current=False` with a bulk `UPDATE`, then inserts the new row with `version = old + 1` and `is_current=True`. History is preserved (old versions stay) but exactly one row is "current." `covered_through_turn` records how far into the conversation the summary reaches.

**Connects to:**
- `app.models.conversation.ConversationSummary` / `MemoryFact`.
- The memory/summarization service in the AI pipeline.

---

### `backend/app/repositories/kb_repo.py`

**Purpose:** Data access for the knowledge base: documents, their chunks, version history, approvals, and — most importantly — full-text (sparse/BM25-style) search over published chunks. This is the sparse half of the app's hybrid retrieval.

**How it works:**

At the top sits the FTS query helper — the file's most interesting logic:
```python
def _or_tsquery_terms(query: str) -> str | None:
    seen: list[str] = []
    for token in re.findall(r"\w+", query.lower()):
        if len(token) > 1 and token not in seen:
            seen.append(token)
    return " | ".join(seen) if seen else None
```
Why it exists: Postgres's `plainto_tsquery` **ANDs** all terms together, so a natural-language question like "VPN error 800 on Windows, how do I fix it?" would only match a chunk containing *every* word — usually nothing. This helper instead:
1. Lowercases and extracts alphanumeric tokens with `re.findall(r"\w+", ...)`.
2. Drops single-character tokens and de-duplicates (preserving order via the `seen` list).
3. Joins them with ` | ` — the `to_tsquery` **OR** operator — so any overlap surfaces a candidate, and `ts_rank` still orders by match quality.

Because the input is reduced to plain alphanumeric tokens, the resulting string is always valid `to_tsquery` syntax — there is no SQL/tsquery injection surface. Returns `None` when nothing usable remains.

`KnowledgeRepository(BaseRepository[KbDocument])`:

Document reads:
- `get_document(document_id, org_id)` — org-scoped fetch that also excludes soft-deleted docs (`deleted_at.is_(None)`).
- `list_published(org_id, category=None, ...)` — only `doc_status == DocStatus.PUBLISHED`, optionally filtered by category, ordered by `last_verified_at DESC NULLS LAST` (freshest verified docs first).
- `search_documents(org_id, q=None, category=None, statuses=None, ...)` — a role-aware admin listing. It starts org-scoped and non-deleted, then conditionally adds a status filter (`doc_status.in_(...)`), a category filter, and a **title** search using `KbDocument.title.ilike(f"%{q}%")` (case-insensitive substring). Ordered by `updated_at DESC NULLS LAST`.
- `count_documents(...)` — the exact same filters as `search_documents` but wrapped in `select(func.count())`, so the UI can show total counts for pagination. (It re-imports `func` locally, which is harmless.)
- `get_by_checksum(org_id, checksum)` — finds a document by content checksum, used to detect duplicate ingestion (don't re-import identical content).

Chunks:
- `list_chunks(doc_id)` — a document's chunks ordered by `chunk_index ASC` (reading order).
- `add_chunk(...)` — inserts one `KbChunk`. It normalizes `chunk_id` to a `uuid.UUID` whether a string or UUID was passed (`chunk_id if isinstance(chunk_id, uuid.UUID) else uuid.UUID(str(chunk_id))`). The comment notes `text_fts` is a **generated column** — Postgres computes the tsvector automatically from `text`, so the repository never sets it.

Versions & approvals:
- `add_version(...)` — appends a `KbDocumentVersion` snapshot (title, status, checksum, optional `snapshot` dict defaulting to `{}`, author). This is the audit trail of document edits.
- `list_versions(doc_id)` — versions ordered `version DESC` (newest first).
- `list_pending_approvals(doc_id)` — `KbApproval` rows for a doc, newest first.

Full-text search — the centerpiece:
```python
async def search_fts(self, org_id, query, *, namespace=None, category=None, limit=20):
    or_terms = _or_tsquery_terms(query)
    if not or_terms:
        return []
    tsquery = func.to_tsquery("english", or_terms)
    rank = func.ts_rank(KbChunk.text_fts, tsquery)
    stmt = (
        select(KbChunk, rank.label("rank"))
        .where(
            KbChunk.org_id == org_id,
            KbChunk.doc_status == DocStatus.PUBLISHED,
            KbChunk.text_fts.op("@@")(tsquery),
        )
    )
    if namespace is not None:
        stmt = stmt.where(KbChunk.retrieval_namespace == namespace)
    if category is not None:
        stmt = stmt.where(KbChunk.category_key == category)
    stmt = stmt.order_by(rank.desc()).limit(limit)
    result = await self.session.execute(stmt)
    return [(row[0], float(row[1] or 0.0)) for row in result.all()]
```
Step by step:
- If the query yields no usable terms, it short-circuits with `[]`.
- `func.to_tsquery("english", or_terms)` builds the OR-joined tsquery using the English text-search config (stemming, stop-words).
- `func.ts_rank(KbChunk.text_fts, tsquery)` scores each chunk's relevance; it is both selected (`.label("rank")`) and used for ordering.
- The `WHERE` restricts to the tenant, to **published** chunks only, and to those that actually match: `KbChunk.text_fts.op("@@")(tsquery)` renders the Postgres `@@` (text-matches) operator. `text_fts` is the generated tsvector column backed by a **GIN index** (defined on the model), which is what makes this fast.
- Optional `namespace` / `category` narrow the search to a category's retrieval namespace.
- Results are ordered by rank descending, capped at `limit`, and returned as `(chunk, score)` tuples. `float(row[1] or 0.0)` guards against a `NULL` rank.

**Connects to:**
- `app.models.knowledge.KbDocument`, `KbChunk`, `KbDocumentVersion`, `KbApproval`.
- `app.core.constants.DocStatus`.
- The retrieval service, which combines these FTS results with dense/vector search (Chroma) for hybrid retrieval; and the KB admin service (listing, versions, approvals, ingestion dedupe).

---

### `backend/app/repositories/ticket_repo.py`

**Purpose:** Data access for `Ticket` and its history (`TicketEvent`, `TicketAssignment`) — queue and engineer worklists, appending audit events, and recording assignment changes.

**How it works:**

`TicketRepository(BaseRepository[Ticket])`:
- `get_by_conversation(conversation_id)` — a ticket is linked 1:1 to a conversation; delegates to base `get_by(conversation_id=...)`.
- `list_queue(org_id, assigned_queue, statuses=None, ...)`:
  ```python
  stmt = select(Ticket).where(
      Ticket.org_id == org_id,
      Ticket.assigned_queue == assigned_queue,
      Ticket.deleted_at.is_(None),
  )
  if statuses:
      stmt = stmt.where(Ticket.status.in_(list(statuses)))
  stmt = stmt.order_by(Ticket.priority.desc(), Ticket.sla_due_at.asc().nullslast()) ...
  ```
  The queue view for support engineers: org- and queue-scoped, optional status filter, ordered by **priority descending, then SLA due date ascending** (highest-priority and soonest-due tickets float to the top — a sensible work-order). `nullslast()` keeps tickets without an SLA at the bottom.
- `list_for_engineer(org_id, engineer_id, statuses=None, ...)` — one engineer's assigned tickets, ordered by `updated_at DESC` (most recently touched first).
- `add_event(...)` — appends a `TicketEvent` capturing `event_type`, optional actor, a `from_status`/`to_status` transition, and a free-form `payload` dict. This is the ticket's immutable history/audit trail.
- `list_events(ticket_id)` — events ordered `created_at ASC` (chronological timeline).
- `record_assignment(...)`:
  ```python
  await self.session.execute(
      update(TicketAssignment)
      .where(TicketAssignment.ticket_id == ticket_id, TicketAssignment.is_current.is_(True))
      .values(is_current=False)
  )
  assignment = TicketAssignment(..., is_current=True)
  self.session.add(assignment)
  await self.session.flush()
  ```
  Same "current pointer" pattern as summaries: it first retires the existing current assignment (bulk `UPDATE is_current=False`), then inserts a new current one. This preserves full assignment history while guaranteeing exactly one current assignment. `assignment_reason` is typed `Any` (an enum passed through from the service).

**Connects to:**
- `app.models.ticket.Ticket`, `TicketEvent`, `TicketAssignment`.
- `app.core.constants.TicketEventType`, `TicketStatus`.
- The ticketing/handoff service (creating tickets from conversations, routing to queues, assigning engineers, and recording state transitions).

---

### `backend/app/repositories/notification_repo.py`

**Purpose:** Data access for user notifications — listing (optionally unread-only), counting unread, and marking notifications read/sent.

**How it works:**

`NotificationRepository(BaseRepository[Notification])`:
- `list_for_user(org_id, user_id, unread_only=False, ...)` — org- and recipient-scoped. When `unread_only` is set it adds `Notification.status != NotificationStatus.READ`. Ordered by `created_at DESC` (newest first). Note "unread" is defined as "not READ," so both freshly-created and merely-sent notifications count as unread until explicitly read.
- `count_unread(org_id, user_id)` — a `func.count()` of notifications for the recipient where `status != READ`. Powers the little unread badge.
- `mark_read(notification)` — sets `status = READ` and stamps `read_at = now`, then flushes.
- `mark_sent(notification)` — sets `status = SENT` and stamps `sent_at = now`, then flushes (used by the delivery worker once a channel actually pushes it).

**Connects to:**
- `app.models.ops.Notification`.
- `app.core.constants.NotificationStatus`.
- The notification service and any background delivery worker.

---

### `backend/app/repositories/feedback_repo.py`

**Purpose:** Data access for user `Feedback` and aggregated `RelevanceSignal` counters that feed the KB's continuous-improvement loop.

**How it works:**

`FeedbackRepository(BaseRepository[Feedback])`:
- `list_unprocessed(limit=100)` — feedback rows where `processed_at IS NULL`, oldest first (`created_at ASC`), so a background job can drain them in arrival order (FIFO).
- `mark_processed(feedback)` — stamps `processed_at = now` so the same feedback isn't handled twice.
- `get_signal(doc_id, chunk_id)`:
  ```python
  stmt = select(RelevanceSignal).where(RelevanceSignal.doc_id == doc_id)
  if chunk_id is None:
      stmt = stmt.where(RelevanceSignal.chunk_id.is_(None))
  else:
      stmt = stmt.where(RelevanceSignal.chunk_id == chunk_id)
  ```
  Finds the aggregate signal row for a document, or a specific chunk within it. The explicit `IS NULL` branch matters: a signal at the *document* level (chunk_id NULL) is a distinct row from a *chunk*-level one, and `== None` wouldn't generate correct SQL — you need `.is_(None)`.
- `upsert_signal(...)`:
  ```python
  signal = await self.get_signal(doc_id, chunk_id)
  if signal is None:
      signal = RelevanceSignal(doc_id=doc_id, chunk_id=chunk_id)
      self.session.add(signal)
  signal.upvotes += upvote_delta
  signal.downvotes += downvote_delta
  signal.impressions += impression_delta
  signal.resolution_success += resolution_success_delta
  await self.session.flush()
  ```
  A read-then-accumulate upsert: it fetches (or creates) the counter row and then applies **deltas** to the running totals rather than overwriting them. These counters (upvotes, downvotes, impressions, resolution successes) let the system learn which documents/chunks actually help resolve tickets.

**Connects to:**
- `app.models.feedback.Feedback` / `RelevanceSignal`.
- The feedback-processing background job and the retrieval-quality/learning loop.

---

### `backend/app/repositories/audit_repo.py`

**Purpose:** Data access for the append-only audit log. Deliberately exposes **only insert and read** — no update or delete — mirroring the database-level INSERT/SELECT-only grant policy (ARCHITECTURE §10).

**How it works:**

`AuditRepository(BaseRepository[AuditLog])`. Although it inherits the base `update`/`delete`/`soft_delete`, the repository's public surface intentionally adds none of those — the class comment states the audit log is write-once.
- `record(...)` — inserts one `AuditLog` capturing org, `action`, `resource_type`, `actor_type` (an `ActorType` enum distinguishing human vs. system/agent actors), optional actor/resource ids, a `trace_id`, and `before`/`after` state dicts plus `ip_address`. Adds + flushes.
- `list_for_org(...)` — overrides the base method (hence `# type: ignore[override]`) with an audit-specific signature. It org-scopes then conditionally filters by `action`, `resource_type`, and `resource_id`, ordered `created_at DESC`, paginated. The trailing `**_: Any` swallows any extra keyword args so it stays call-compatible with the base signature.
- `list_by_trace(trace_id)` — every audit entry sharing a `trace_id`, ordered `created_at ASC`. Because a single user action flows through many components under one trace id, this reconstructs the full chronological story of that request.

**Connects to:**
- `app.models.ops.AuditLog`.
- `app.core.constants.ActorType`.
- Services across the app that must leave an audit trail (auth, ticket changes, KB approvals, admin actions).

---

### `backend/app/repositories/analytics_repo.py`

**Purpose:** Data access for the analytics event stream (`AnalyticsEvent`) and precomputed usage rollups (`UsageStatistic`) — recording events and aggregating counts for dashboards.

**How it works:**

`AnalyticsRepository(BaseRepository[AnalyticsEvent])`:
- `record_event(...)` — inserts one event with `event_type`, optional `user_id`/`conversation_id`/`ticket_id`/`category`, and a `properties` dict (defaulting to `{}` so the JSON column is never NULL). Adds + flushes.
- `count_by_type(org_id, event_type, since=None)`:
  ```python
  select(func.count()).select_from(AnalyticsEvent).where(
      AnalyticsEvent.org_id == org_id,
      AnalyticsEvent.event_type == event_type,
  )
  if since is not None:
      stmt = stmt.where(AnalyticsEvent.occurred_at >= since)
  ```
  Counts events of one type for a tenant, optionally only since a timestamp (for "last 24h / last 7d" style metrics).
- `counts_grouped_by_type(org_id, since=None)`:
  ```python
  select(AnalyticsEvent.event_type, func.count())
    .where(AnalyticsEvent.org_id == org_id)
    .group_by(AnalyticsEvent.event_type)
  ...
  return {row[0]: int(row[1]) for row in result.all()}
  ```
  A single `GROUP BY` query returning a `{event_type: count}` dictionary — one round-trip to populate a whole dashboard chart, again optionally time-bounded.
- `list_rollups(org_id, metric_key=None, ...)` — reads precomputed `UsageStatistic` rows (org-scoped, optional `metric_key` filter), ordered by `period_start DESC`, paginated. These are the batch-aggregated metrics a background job writes, so dashboards read cheap summaries instead of re-scanning the raw event stream.

**Connects to:**
- `app.models.ops.AnalyticsEvent` / `UsageStatistic`.
- The analytics/dashboard service and the rollup background job.

---

### How the pieces fit together

A typical request flows like this: FastAPI resolves `get_session` (session.py) → yields one `AsyncSession` bound to the shared async `engine` → a service constructs the repositories it needs, passing that session in → repositories `add`/`flush` rows and run `select`s but never commit → when the handler returns, `get_session` issues one `COMMIT` (or `ROLLBACK` on error) and closes the session. Meanwhile `db/base.py` + `migrations/` define and evolve the schema those repositories read and write, with the ORM models as the single source of truth (built once by `0001`, extended by LangGraph in `0002`, and seeded with baseline data in `0003`).

Key relevant files (all absolute):
- `backend\app\db\session.py`
- `backend\app\db\base.py`
- `backend\app\db\migrations\env.py`
- `backend\app\db\migrations\script.py.mako`
- `backend\app\db\migrations\versions\0001_initial_schema.py`
- `backend\app\db\migrations\versions\0002_checkpointer_setup.py`
- `backend\app\db\migrations\versions\0003_seed_roles_and_categories.py`
- `backend\app\repositories\base.py`
- `...\repositories\{user_repo,conversation_repo,memory_repo,kb_repo,ticket_repo,notification_repo,feedback_repo,audit_repo,analytics_repo}.py`

---

I now have all the code. Here is the walkthrough.

## Backend — Providers & Registries

This section covers two closely related packages:

- `backend/app/providers/` — the **model-provider abstraction**. It defines the contracts every AI model backend must satisfy (LLM, embeddings, verifier), the shared "plumbing" (rate limiting, retries, timeouts, token counting), the concrete adapters for Gemini / OpenAI / Claude, a set of deterministic fakes for tests, an LLM-as-judge verifier, and a factory that hands out the right provider based on settings.
- `backend/app/registries/` — the **data-driven extensibility layer**. These are small lookup tables (categories, thresholds, prompts, tools) that let you reconfigure the assistant's behavior without touching engine code, and optionally override the in-code defaults from the database.

The guiding design idea in both packages is: **program against contracts, not concrete vendors.** The engine never says "call Gemini." It says "give me an `LLMProvider`" and the factory decides which vendor that is, based on configuration. Swapping OpenAI for Claude is a one-line settings change.

---

### `providers/base.py`

**Purpose**
The foundation of the whole provider system. It defines (1) the plain data objects passed around (`ChatMessage`, `LLMResult`, etc.), (2) the `Protocol` contracts that describe what a provider must be able to do, (3) the cross-cutting resilience utilities (rate limiter, retry, token accountant), and (4) two abstract base classes that give concrete adapters all the plumbing for free so each vendor adapter only has to implement the raw network call.

**How it works**

*Value objects (the data that flows through the system).* These are all `@dataclass(frozen=True)`, meaning immutable — once created they cannot be mutated, which makes them safe to pass around and cache.

- `ChatMessage` is a single conversation turn: a `role` (`system | user | assistant | tool`) plus `content` text. This is the vendor-neutral message format; each adapter later translates it into that vendor's specific format.
- `TokenUsage` records `prompt_tokens`, `completion_tokens`, `total_tokens`. It defines `__add__`, so you can literally write `usage_a + usage_b` and get a combined total — this is used to accumulate cost across many calls:
  ```python
  def __add__(self, other: TokenUsage) -> TokenUsage:
      return TokenUsage(
          self.prompt_tokens + other.prompt_tokens,
          self.completion_tokens + other.completion_tokens,
          self.total_tokens + other.total_tokens,
      )
  ```
- `LLMResult` is what a text-generation call returns: the `text`, which `model` produced it, the `tier` (`small`/`large`), a `finish_reason`, and the `usage`.
- `EmbeddingResult` holds the `vectors` (a list of float lists — one vector per input text), the `model`, the vector `dim`ension, and `usage`.
- `VerifierResult` is the output of a grounding check: `entailed` (bool — is the claim supported?), a `score` in [0,1], and a human-readable `rationale`.

*Protocols (the contracts).* A `Protocol` is Python's way of expressing "structural typing" (a.k.a. duck typing with type-checker support): any class that has the right attributes and methods automatically satisfies the protocol — it does **not** need to inherit from it. `@runtime_checkable` additionally lets you use `isinstance(obj, LLMProvider)` at runtime.

- `LLMProvider` requires a `model_id` and `tier` attribute plus three methods: `generate` (one-shot completion), `stream` (async iterator of text chunks), and `generate_structured` (return a validated object matching a schema).
- `EmbeddingProvider` requires `model_id`, `dim`, and an `embed(texts)` method.
- `VerifierProvider` requires a single `verify(claim, sources)` method.

This is why the fakes in `fakes/providers.py` can be used interchangeably even though they do **not** subclass anything — they just happen to have the right shape.

*Resilience utilities.*

`AsyncRateLimiter` is a sliding-window limiter allowing `max_per_minute` calls per rolling 60 seconds. `acquire()` is `async` and guarded by an `asyncio.Lock` so concurrent coroutines don't corrupt the shared state. Its logic:
```python
now = time.monotonic()
while self._events and now - self._events[0] >= 60.0:
    self._events.popleft()          # drop timestamps older than 60s
if len(self._events) >= self.max_per_minute:
    sleep_for = 60.0 - (now - self._events[0])
    ...
    await asyncio.sleep(max(0.0, sleep_for))   # wait until oldest ages out
self._events.append(time.monotonic())
```
It keeps a `deque` of call timestamps, evicts anything older than 60 seconds, and if the window is still full it sleeps until the oldest entry expires. `time.monotonic()` is used (not wall-clock) so it is immune to system clock changes. A `max_per_minute <= 0` disables limiting entirely.

`TokenAccountant` accumulates `TokenUsage` per model name for cost attribution. `record()` is async + lock-protected and uses the `__add__` operator defined earlier. `snapshot()` returns a copy of the per-model dict, and the `total` property sums every model's usage into one grand total. A **single** accountant instance is shared process-wide (wired in `registry.py`), so it acts as a global cost meter.

`retry_async` is the generic retry wrapper. It takes a `factory` — a zero-argument function returning an awaitable — and retries it with exponential backoff plus jitter:
```python
for attempt in range(retries + 1):
    try:
        return await factory()
    except ProviderError:
        raise                        # our own errors are terminal, don't retry
    except Exception as exc:
        last_exc = exc
        if attempt >= retries:
            break
        delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
        ...
        await asyncio.sleep(delay)
raise ProviderError(f"{what} failed after {retries + 1} attempts: {last_exc}")
```
Two subtle points worth understanding as an early-career dev: (1) It takes a **factory** rather than a coroutine, because a coroutine can only be awaited once — to retry you must create a fresh awaitable each attempt. (2) `ProviderError` is re-raised immediately instead of being retried; those represent deliberate, non-transient failures (e.g. "API key not configured"), so retrying is pointless. The `2**attempt` term doubles the wait each round; `random.uniform(0, base_delay)` adds jitter so many clients retrying at once don't stampede in lockstep.

*Abstract base adapters.* This is the heart of the "DRY plumbing" idea.

`BaseLLMProvider(ABC)` takes everything it needs via keyword-only constructor args: `model`, `tier`, a shared `rate_limiter`, a shared `accountant`, plus `timeout`, `max_retries`, `base_delay`, `temperature`, `max_output_tokens`. It declares two abstract methods that subclasses **must** implement — `_acomplete` (the raw completion call) and `_astream` (the raw streaming call) — and then provides the fully-implemented public methods that wrap them:

- `generate()` acquires a rate-limit slot, wraps the `_acomplete` call in `asyncio.wait_for(..., timeout=self._timeout)` (so a hung request is cancelled), runs it through `retry_async`, then records usage with the accountant. So every adapter automatically gets rate limiting + timeout + retry + accounting without writing a line of it.
  ```python
  async def _call() -> LLMResult:
      return await asyncio.wait_for(self._acomplete(messages, **kwargs), timeout=self._timeout)
  result = await retry_async(_call, retries=self._max_retries, base_delay=self._base_delay,
                             what=f"{type(self).__name__}.generate({self.model_id})")
  await self._accountant.record(self.model_id, result.usage)
  ```
- `stream()` is an async generator: it acquires the limiter, then yields tokens from `_astream`, translating any unexpected exception into a `ProviderError` (while letting real `ProviderError`s pass through). Note streaming is intentionally **not** retried — you cannot cleanly restart a half-emitted stream.
- `generate_structured()` is a clever default that turns any text LLM into a structured-output engine. It builds a `schema_hint` (JSON schema string) via `_schema_hint`, prepends a system message ordering the model to *"Respond with ONLY a single valid JSON object … match this schema"*, calls `generate()`, extracts the JSON with `_extract_json`, and — if the schema is a Pydantic `BaseModel` subclass — validates it via `schema.model_validate(payload)`. Otherwise it returns the raw dict.

`BaseEmbeddingProvider(ABC)` mirrors this for embeddings. Its one abstract method is `_aembed(texts) -> list[list[float]]`. Its public `embed()` does rate-limit → timeout → retry, then computes a rough usage estimate as *word count* of the inputs (`sum(len(t.split()) for t in texts)`), records it, and wraps everything into an `EmbeddingResult`. The word-count is a deliberate approximation since embedding APIs don't always report tokens uniformly.

*Helpers.* `_schema_hint` serializes a Pydantic model's JSON schema, a plain dict, or falls back to `str()`. `_extract_json` is defensive parsing for messy LLM output: it strips Markdown code fences (```` ``` ```` and a leading `json` language tag), finds the first `{` and last `}`, and `json.loads` that slice — raising `ProviderError` if no object is found or JSON is invalid. This tolerance matters because models frequently wrap JSON in prose or fences despite instructions.

**Connects to**
- Imports `ProviderError` from `app.core.exceptions` and `get_logger` from `app.core.logging`.
- Its Protocols and base classes are the parent of / contract for every adapter in `gemini/`, `openai/`, `claude/`, `fakes/`, and `verifier/`.
- `registry.py` constructs the shared `AsyncRateLimiter` and `TokenAccountant` and injects them into the base-class constructors.
- The engine/agent nodes consume `ChatMessage`, `LLMResult`, and the Protocol types throughout.

---

### `providers/__init__.py`

**Purpose**
The public front door of the providers package — it re-exports the value objects, Protocols, and factory functions so the rest of the codebase can write `from app.providers import get_llm_provider, ChatMessage` instead of reaching into submodules.

**How it works**
Pure re-export module. It pulls `ChatMessage`, `TokenUsage`, `LLMResult`, `EmbeddingResult`, `VerifierResult`, and the three Protocols from `base`, and the four factory functions (`get_llm_provider`, `get_embedding_provider`, `get_verifier_provider`, `reset_provider_cache`) from `registry`, then lists them all in `__all__`.

**Connects to** `base.py` and `registry.py`. It is what callers elsewhere in the app import from.

---

### `providers/registry.py`

**Purpose**
The **provider factory**. It reads settings, decides which vendor is active, builds the right adapter for the requested tier, and caches instances so everyone shares one rate limiter and one token accountant. This is the single place that knows about all concrete vendors.

**How it works**

Module-level state is the key concept here: a single `_accountant = TokenAccountant()`, a lazily-created `_rate_limiter`, and two caches — `_llm_cache` keyed by `(provider, tier)` and `_embed_cache` keyed by provider name. Because these are module globals, they behave like process-wide singletons.

- `get_token_accountant()` exposes the shared accountant so other code can read total cost.
- `_limiter()` lazily builds the `AsyncRateLimiter` from `settings.LLM_RATE_LIMIT_PER_MINUTE` on first use, then reuses it.
- `_llm_kwargs(settings, tier)` bundles the common constructor arguments (tier, the shared limiter/accountant, timeout, retries, delay, temperature, max output tokens) into one dict so each adapter is built the same way.
- `_build_llm(provider, tier, settings)` is the dispatch. For `"fake"` it returns `FakeLLMProvider(tier=tier)` (no key needed). For the real vendors it picks the model name by tier — e.g. for Gemini `model = settings.LLM_SMALL_MODEL if tier == "small" else settings.LLM_LARGE_MODEL` — and constructs `GeminiLLMProvider` / `OpenAILLMProvider` / `ClaudeLLMProvider` with `**_llm_kwargs(...)`. An unknown provider raises `ProviderError`.
- `get_llm_provider(tier="large")` reads `settings.LLM_PROVIDER.lower()`, forms the cache key `(provider, tier)`, builds-and-caches on a miss, and returns the cached instance. So the first call for `("gemini","small")` constructs it and every later call reuses it.
- `_build_embedding` / `get_embedding_provider` do the same for embeddings, keyed by provider name only (embeddings have no tier). Note **Claude has no embedding adapter** — the branch simply isn't there, so if `EMBEDDING_PROVIDER=claude` you'd hit the `ProviderError("Unknown embedding provider")`. Embeddings are expected to come from Gemini/OpenAI/fake.
- `get_verifier_provider()` has a deliberate policy baked in. For `fake` it returns `FakeVerifierProvider()`. Otherwise it returns `LLMVerifier(get_llm_provider("small"))` — i.e. the grounding judge deliberately runs on the **small/cheap tier**. The inline comment explains why: the synthesizer already burns the large tier, so using the lite model for the judge keeps per-turn call count and rate-limit pressure down, and transient judge failures are handled gracefully downstream by the grounding gate.
- `reset_provider_cache()` clears both caches and nulls the limiter — used by tests after they change settings, so stale cached providers don't leak between test cases.
- The line `_ = (BaseLLMProvider, BaseEmbeddingProvider)` is a no-op that keeps those imports "used" for type consumers / linters.

**Connects to**
- `app.core.config` for `Settings` / `get_settings`, `app.core.exceptions` for `ProviderError`.
- Imports every concrete adapter: `claude`, `fakes`, `gemini`, `openai`, and the `verifier.LLMVerifier`.
- Its four functions are re-exported by `providers/__init__.py` and called throughout the agent engine whenever a model is needed.

---

### `providers/gemini/llm.py`

**Purpose**
Concrete `LLMProvider` for Google Gemini via the `google-generativeai` SDK, with special handling to work behind corporate TLS-inspection proxies.

**How it works**
`GeminiLLMProvider` subclasses `BaseLLMProvider`, so it only implements the raw calls; all resilience is inherited.

- `_to_gemini(messages)` is a module-level translator from the neutral `ChatMessage` list to Gemini's format. It concatenates all `system` messages into a single `system_instruction` string, and maps the rest into Gemini's `contents` list, where role `assistant` becomes Gemini's `"model"` and everything else becomes `"user"`:
  ```python
  contents = [
      {"role": "model" if m.role == "assistant" else "user", "parts": [m.content]}
      for m in messages if m.role in ("user", "assistant")
  ]
  ```
- `_client()` lazily configures the SDK. It reads the key with `settings.GEMINI_API_KEY.get_secret_value()` (a Pydantic `SecretStr`, so the key never prints accidentally) and raises `ProviderError` if it's missing. It then best-effort calls `truststore.inject_into_ssl()` so Python uses the OS certificate store (this is what lets it survive corporate TLS interception), swallowing any failure. It imports `google.generativeai` **inside the method** (lazy import) so the SDK is only required if Gemini is actually used, and configures `transport="rest"` — the comment notes REST honors Python's SSL/truststore whereas gRPC does not.
- `_generation_config` merges per-call `kwargs` over the instance defaults for `temperature` and `max_output_tokens`.
- `_acomplete` builds a `GenerativeModel` with the system instruction, then runs the **synchronous** `model.generate_content(...)` inside `asyncio.to_thread(...)`. This is important: the Gemini REST call is blocking, and wrapping it in a worker thread keeps the async event loop from stalling. It reads `usage_metadata` defensively with `getattr(..., 0) or 0` and returns an `LLMResult`.
- `_astream` does **not** truly stream — it awaits `_acomplete` and yields the whole text as one chunk. The module docstring explains this is fine because the engine's synthesizer calls `generate`, not `stream`, so the REST sync path suffices.

**Connects to** `BaseLLMProvider` and the value objects in `base.py`; `app.core.config` and `app.core.exceptions`; instantiated by `registry.py`.

---

### `providers/gemini/embeddings.py`

**Purpose**
Concrete `EmbeddingProvider` for Gemini embeddings.

**How it works**
`GeminiEmbeddingProvider(BaseEmbeddingProvider)` implements only `_aembed`. Its `_client()` is identical in spirit to the LLM adapter's (secret key, truststore injection, lazy import, REST transport). `_aembed` embeds each text with the blocking `genai.embed_content(...)` call, wrapping each in `asyncio.to_thread`, and runs them concurrently via `asyncio.gather`:
```python
return await asyncio.gather(*(asyncio.to_thread(_embed_one, t) for t in texts))
```
So a batch of N texts fires N embedding calls in parallel worker threads rather than serially. Each `_embed_one` returns `list(result["embedding"])`.

**Connects to** `BaseEmbeddingProvider`; built by `registry.py` when `EMBEDDING_PROVIDER=gemini`.

---

### `providers/gemini/__init__.py`

**Purpose / How it works** Re-exports `GeminiLLMProvider` and `GeminiEmbeddingProvider` so `registry.py` can import them from the package root.

---

### `providers/openai/llm.py`

**Purpose**
Concrete `LLMProvider` for OpenAI using the modern `openai>=1.x` `AsyncOpenAI` client. Unlike Gemini, this SDK is natively async, so it supports true streaming.

**How it works**
`OpenAILLMProvider(BaseLLMProvider)`.

- `_client()` reads `OPENAI_API_KEY` (SecretStr), raises `ProviderError` if empty, lazily imports `AsyncOpenAI`, and returns a client wired with the adapter's `timeout`.
- `_acomplete` calls `await client.chat.completions.create(...)`, converting each `ChatMessage` straight to OpenAI's `{"role": ..., "content": ...}` dicts (OpenAI's roles already match the neutral roles). It maps per-call overrides for `temperature`/`max_tokens`, reads `response.choices[0]`, and returns an `LLMResult` with the finish reason and token usage from `response.usage`.
- `_astream` passes `stream=True` and iterates chunks, yielding `chunk.choices[0].delta.content` whenever it is non-empty:
  ```python
  async for chunk in stream:
      delta = chunk.choices[0].delta.content if chunk.choices else None
      if delta:
          yield delta
  ```
  This is genuine token-by-token streaming.

**Connects to** `BaseLLMProvider`; built by `registry.py` when `LLM_PROVIDER=openai`.

---

### `providers/openai/embeddings.py`

**Purpose** Concrete `EmbeddingProvider` for OpenAI.

**How it works**
`OpenAIEmbeddingProvider(BaseEmbeddingProvider)`. Same lazy `_client()` pattern. `_aembed` is a single batched call — OpenAI's embeddings endpoint accepts a list of inputs directly — so it just does `await client.embeddings.create(model=..., input=texts)` and returns `[list(item.embedding) for item in response.data]`. This is simpler and more efficient than Gemini's per-text fan-out.

**Connects to** `BaseEmbeddingProvider`; built by `registry.py` when `EMBEDDING_PROVIDER=openai`.

---

### `providers/openai/__init__.py`

**Purpose / How it works** Re-exports `OpenAILLMProvider` and `OpenAIEmbeddingProvider`.

---

### `providers/claude/llm.py`

**Purpose**
Concrete `LLMProvider` for Anthropic Claude via `AsyncAnthropic`. LLM only — there is deliberately no Claude embedding adapter (Anthropic doesn't offer embeddings; use Gemini/OpenAI for that).

**How it works**
`ClaudeLLMProvider(BaseLLMProvider)`.

- `_split_system(messages)` handles a key Anthropic quirk: the Messages API takes the system prompt as a **separate top-level `system` parameter**, not as a message in the list. So it joins all system messages into one string and builds a `turns` list of only user/assistant messages.
- `_client()` follows the same pattern (SecretStr `ANTHROPIC_API_KEY`, `ProviderError` if missing, lazy import, timeout).
- `_acomplete` calls `client.messages.create(model=..., system=system or "", messages=turns, ...)`. Claude returns content as a list of typed blocks, so the text is reassembled from only the text blocks:
  ```python
  text = "".join(block.text for block in response.content if block.type == "text")
  ```
  Token usage is mapped from Anthropic's names — `input_tokens` → prompt, `output_tokens` → completion — and `total` is computed by summing them (Anthropic doesn't return a combined total). `finish_reason` comes from `response.stop_reason`.
- `_astream` uses Anthropic's `async with client.messages.stream(...) as stream:` context manager and yields from `stream.text_stream` — genuine streaming.

**Connects to** `BaseLLMProvider`; built by `registry.py` when `LLM_PROVIDER=claude`.

---

### `providers/claude/__init__.py`

**Purpose / How it works** Re-exports `ClaudeLLMProvider` only (note: no embedding export, reflecting the LLM-only support).

---

### `providers/fakes/providers.py`

**Purpose**
Deterministic, network-free "test doubles" for all three provider types. They make the entire engine runnable in CI with no API keys and no external calls, and — importantly — they are **steerable**, so tests can force specific routing decisions (e.g. high-confidence "deliver" vs low-confidence "escalate").

**How it works**
Note these classes **do not inherit** from the base classes — they just implement the Protocol shape, demonstrating the structural-typing point from `base.py`.

*Schema fabrication helpers.* `_fab_value(annotation)` invents a plausible default value for a type annotation. It inspects the typing origin: `Optional`/`Union` unwraps to the first non-`None` arg; `list/set/tuple` → `[]`; `dict` → `{}`. For concrete classes it returns type-appropriate stubs — an Enum's first member value, `False` for bool, `0` for int, `0.9` for float (notably high, so confidence-style fields default to "confident"), `""` for str, and a random UUID string for `UUID`. `_fabricate(schema)` walks a Pydantic model's `model_fields` and fills only the **required** ones with fabricated values. This lets the fake satisfy any schema the engine asks for.

*`FakeLLMProvider`.* Constructed with a canned `text` (default `"Based on the knowledge base, here is the resolution [1]."` — note it includes a citation so grounding checks pass) and an optional `structured` override dict keyed by schema class name. 
- `generate` returns the canned text with word-count-based token usage.
- `stream` yields the text word by word (re-appending spaces) to mimic streaming.
- `generate_structured` is the steerable part: it looks up the schema's class name in `self._structured`; if a test supplied an override it uses that, otherwise it falls back to `_fabricate(schema)`. It then validates through the Pydantic model. So a test can pass `structured={"RouterVerdict": {...}}` to force a particular route.

*`FakeEmbeddingProvider`.* Produces a deterministic unit vector from the SHA-256 hash of the text: it turns hash bytes into floats in [0,1], then L2-normalizes them (dividing by the vector norm, guarded by `or 1.0` against divide-by-zero). Deterministic means the same text always yields the same vector, so similarity tests are reproducible.

*`FakeVerifierProvider`.* A cheap lexical-overlap stand-in for the real NLI judge. It lowercases and keeps words longer than 3 chars from the claim and the sources, computes `overlap = |claim∩source| / |claim|`, and returns `entailed = overlap >= threshold` (default 0.05) with the ratio as the score. An empty claim is treated as trivially entailed.

**Connects to** value objects and Protocols in `base.py`; all three are instantiated by `registry.py` when the corresponding provider setting is `"fake"`. Used pervasively by the test suite.

---

### `providers/fakes/__init__.py`

**Purpose / How it works** Re-exports the three fakes.

---

### `providers/verifier/nli_verifier.py`

**Purpose**
The **LLM-as-judge grounding verifier** — reliability gate #2's engine. It wraps a (small-tier) `LLMProvider` to decide whether a synthesized answer (the "claim") is fully supported by the retrieved knowledge-base "sources," returning a structured `VerifierResult`. This is how the assistant catches hallucinations before delivering an answer.

**How it works**
`LLMVerifier(llm)` stores the injected `LLMProvider`. The module defines a strict `_SYSTEM` prompt that instructs the model to act as a *"strict fact-verification judge,"* to use **only** the given sources (no outside knowledge), to treat any unsupported or contradicted part as **not entailed**, and to return a faithfulness score in [0,1].

`_Verdict` is a Pydantic model with safe defaults (`entailed=False`, `score=0.0` constrained `ge=0.0, le=1.0`, `rationale=""`), so even a partial/garbled model response validates.

`verify(claim, sources)`:
- Short-circuits: if there are no sources it immediately returns `entailed=False, score=0.0, rationale="no sources"` — you can't be grounded in nothing.
- Otherwise it renders sources as a numbered list (`[1] ...`, `[2] ...`) and builds a two-message chat: the system judge prompt plus a user message laying out `CLAIM` and `SOURCES` and asking *"Is the claim fully supported by the sources?"*
- It calls `await self._llm.generate_structured(messages, _Verdict)` — reusing the base class's structured-output machinery — and maps the `_Verdict` into a `VerifierResult`.

**Connects to** `ChatMessage`, `LLMProvider`, `VerifierResult` from `base.py`; instantiated by `registry.get_verifier_provider()` with the small-tier LLM. Its `VerifierResult` feeds the grounding gate in the agent engine.

---

### `providers/verifier/__init__.py`

**Purpose / How it works** Re-exports `LLMVerifier`.

---

### `registries/__init__.py`

**Purpose / How it works**
The registries package front door. It currently re-exports only `PromptRegistry` and `get_prompt_registry`. (The category, threshold, and tool registries are imported directly from their submodules by their consumers rather than surfaced here.)

---

### `registries/category_registry.py`

**Purpose**
The central **data-driven extensibility seam**. A "category" is a type of helpdesk issue (login, VPN, payment, …), and each carries its own configuration: which retrieval namespace to search, SLA tier, which human queue to hand off to, what intake fields to collect, its confidence thresholds, and which tools it may use. Crucially, it ships with in-memory defaults so the engine works with **no database at all**, and can later be overridden from Postgres.

**How it works**

`CategoryConfig` (frozen dataclass) is one category's full configuration: `category_key`, `display_name`, `retrieval_namespace`, `sla_tier`, `handoff_queue`, plus `required_intake_fields` (a slot-name→type dict), `thresholds` (float dict), `tool_bindings` (list of tool names), and `is_active`.

`_DEFAULT_TOOLS = ["search_kb", "semantic_search", "create_ticket", "get_conversation"]` is the standard tool set given to every category.

`DEFAULT_CATEGORIES` hard-codes the **8 canonical seed categories** (mirroring DB migration 0003): `login_issue`, `password_reset`, `vpn`, `payment`, `software_install`, `application_error`, `email`, `hardware_request`. Study `payment` to see the design intent — it is deliberately the strictest:
```python
"payment": CategoryConfig("payment", "Payment Issues", "payment", "priority", "billing",
    {"invoice_id": "string", "amount": "string", "payment_method": "string"},
    {"retrieval": 0.82, "deliver": 0.88, "grounding_min": 0.82, "retry_budget": 0}, _DEFAULT_TOOLS),
```
It uses the `"priority"` SLA tier, routes to the `"billing"` queue, demands three intake slots, and sets much higher thresholds with **zero** retry budget — the system is far more cautious about anything money-related.

Below the canonical 8, an **extended set** of ~29 enterprise demo categories is added (`mfa`, `wifi`, `outlook`, `printer`, `docker`, `python_env`, …). This is additive by design:
```python
for _k, _n, _q, _slots in _EXTRA_CATEGORIES:
    DEFAULT_CATEGORIES.setdefault(_k, CategoryConfig(_k, _n, _k, "standard", _q, dict(_slots), dict(_STD_TH), list(_DEFAULT_TOOLS)))
```
`setdefault` guarantees the extras **never clobber** the canonical 8 (if a key already exists it's left alone). They share a looser standard threshold set `_STD_TH` (retrieval 0.60 / deliver 0.66 / grounding_min 0.60 / retry 1). Most have no required slots (empty dict → answer directly), but a handful pass `_GUIDED = {"issue_type": "string"}` — that single slot is what triggers the guided quick-reply clarification flow, so `wifi`, `outlook`, `teams`, `browser`, `printer` will ask a clarifying question. Note the fresh `dict(...)`/`list(...)` copies per category so they don't accidentally share mutable state.

`FALLBACK_CATEGORY` is a "General" config (backed by the `application_error` namespace/queue) returned whenever a message can't be classified into a known category.

`CategoryRegistry` wraps the dict. `keys()` returns only **active** category keys. `get(key)` returns the matching config or `FALLBACK_CATEGORY` if the key is unknown/`None` — so callers never get a `KeyError`. `required_slots(key)` returns the intake field names for a category. `load_from_db(session)` is the DB override path: it selects active `CategoryRegistry` rows from Postgres and replaces the in-memory entries, carefully handling the `tool_bindings` column whether it's stored as a `{"tools": [...]}` dict or a bare list; it returns how many rows it loaded.

`get_category_registry()` is a lazy singleton accessor (module-global `_registry`, created on first call).

**Connects to**
- `app.models.registry.CategoryRegistry` (the ORM row model) — imported lazily inside `load_from_db`.
- Consumed by `threshold_registry.py` (for the thresholds dict) and `tool_registry.py` (for `tool_bindings`), and by the router/agent engine for namespaces, SLA, handoff queues, and required slots.

---

### `registries/threshold_registry.py`

**Purpose**
Resolves the confidence gate thresholds (retrieval / deliver / grounding-min) and retry budget for a category, and **tightens** them for high-sensitivity turns like payment or security.

**How it works**
`ThresholdSet` (frozen dataclass) holds the four numbers. `_DEFAULTS` provides safe fallbacks (0.72 / 0.75 / 0.70 / 1).

`ThresholdRegistry` is constructed with a `CategoryRegistry` (defaulting to the shared singleton). Its one real method, `for_category(category_key, sensitivity=LOW)`:
1. Pulls the category's raw `thresholds` dict from the category registry.
2. Builds a `base` `ThresholdSet`, reading each value with `.get(key, default)` so missing keys fall back safely, and coercing types (`float(...)`, `int(...)`).
3. If `sensitivity == SensitivityLevel.HIGH`, it returns a **tightened** set — each float threshold bumped by `+0.08` (capped at 0.99 via `min`) and the retry budget reduced by 1 (floored at 0 via `max`):
   ```python
   return ThresholdSet(
       retrieval=min(0.99, base.retrieval + 0.08),
       deliver=min(0.99, base.deliver + 0.08),
       grounding_min=min(0.99, base.grounding_min + 0.08),
       retry_budget=max(0, base.retry_budget - 1),
   )
   ```
   So on a sensitive turn the assistant demands higher confidence and won't retry — it prefers escalating to a human over risking a wrong answer. `LOW`/`MEDIUM` sensitivity returns the base set unchanged.

`get_threshold_registry()` is the lazy-singleton accessor.

**Connects to** `SensitivityLevel` from `app.core.constants`; `CategoryRegistry`/`get_category_registry` from `category_registry.py` (its data source). Consumed by the agent engine's confidence/grounding gates to decide deliver-vs-clarify-vs-escalate.

---

### `registries/prompt_registry.py`

**Purpose**
The **prompt library**. It holds the versioned system/user prompt templates for every agent node, renders them into provider `ChatMessage` lists, and supports DB overrides. This is where the assistant's "personality" and output contracts live, editable without touching engine logic.

**How it works**

*Safe formatting.* `_SafeDict` subclasses `dict` and overrides `__missing__` to return `""` — so if a template references a placeholder you didn't supply, you get an empty string instead of a `KeyError`. `_safe_format` uses `string.Formatter().vformat(template, (), _SafeDict(variables))` to apply it. This makes rendering forgiving of missing variables.

*`PromptTemplate`* (frozen dataclass): a `key`, the `node_id` it belongs to, the `system` text, a `user_template` (default `"{input}"`), a `version`, and the tuple of expected `variables` (documentation of what the template consumes). Its `render(**variables)` produces exactly two messages — a formatted system message and a formatted user message:
```python
def render(self, **variables):
    return [
        ChatMessage(role="system", content=_safe_format(self.system, variables)),
        ChatMessage(role="user", content=_safe_format(self.user_template, variables)),
    ]
```

*`DEFAULT_PROMPTS`* is the in-code library. Each entry ties a prompt to an agent node:

- **`router`** (node `intent_classifier`): classifies the message into exactly one of `{categories}`, and extracts intent, sensitivity (payment/security = high), a control intent (`greeting|cancel|human_request|none`), which `{required_slots}` are still missing, and an `intent_confidence` in [0,1]. Its user template feeds the conversation `{summary}` and the `{input}`.
- **`retriever`** (node `query_planner`): rewrites the user message into **one** concise standalone keyword search query for `{category}`, resolving pronouns from the summary, and is strictly told to return only the query text on a single line — no labels, quotes, or alternatives — because the output is fed directly into search.
- **`knowledge`** (node `solution_synthesizer`): the main grounded-answer prompt. It orders the model to answer **strictly** from numbered `SOURCES`, never invent, cite inline as `[n]`, and — critically — reply with exactly `ABSTAIN` if the sources lack a reliable answer (this single token is what the grounding/abstain logic keys on). It then mandates a precise Markdown template with fixed sections (`### Issue Detected`, `### Likely Cause`, `### Recommended Steps` with checkbox items, `### If the steps don't help`, `### Need more help?`) and even a `> **Success:**` blockquote. It supplies `{input}` and `{context}` (the rendered sources).
- **`general`** (node `solution_synthesizer`): the **fallback** synthesizer used when the KB has no matching article. It forces the answer to begin with the exact disclaimer line `> **Note:** General guidance — not from our knowledge base.`, then give safe best-practice steps, offer a support ticket for account-specific issues, and explicitly **not** cite sources or invent company details. This is the safe "we don't have a KB article but here's general help" path.
- **`clarification`** (node `info_collector`): asks for the `{missing_slots}` for `{category}` in one friendly batched question, avoiding anything already in `{filled_slots}`.
- **`ticket`** (node `ticket_creator`): assembles an engineer-ready ticket (subject, problem summary, suggested priority, tags) from the transcript and collected fields, facts-only.
- **`summarizer`** (node `memory_manager`): merges the prior rolling summary with new turns into a ≤150-word compact summary, preserving key identifiers.
- **`memory_updater`** (node `memory_manager`): extracts durable, reusable user facts (default_device, vpn_client, os) as key/value pairs, ignoring one-off or sensitive values.
- **`escalation`** (node `human_handoff`): writes a factual handoff briefing for a human engineer including the `{reason_code}`.

*`PromptRegistry`.* Wraps the dict. `keys()` lists template keys; `get(key)` raises `KeyError` on unknown keys (unlike the category registry, prompts have no silent fallback — a missing prompt is a programming error); `render(key, **vars)` is the convenience one-liner used by nodes; `register(template)` adds/replaces a template at runtime. `load_from_db(session)` overrides code defaults with active `prompt_templates` rows — but note it **only** overrides keys that already exist in code, and it replaces just the `system` content and `version` while **keeping the code's `user_template` and `variables`** (the DB stores the system content, code owns the structural wiring). It returns the count overridden.

`get_prompt_registry()` is the lazy-singleton accessor.

**Connects to** `ChatMessage` from `app.providers.base` (its render output); `app.models.registry.PromptTemplate` (ORM row) lazily inside `load_from_db`. Every agent node imports the rendered messages from here and feeds them to an `LLMProvider`.

---

### `registries/tool_registry.py`

**Purpose**
Resolves tool ids to actual `Tool` objects and answers "which tools may this category use?" — the binding between the category config's `tool_bindings` list and the concrete tool implementations.

**How it works**
`ToolRegistry` is constructed with a tools dict (defaulting to `BUILTIN_TOOLS` from `app.agents.tools`) and a `CategoryRegistry` (defaulting to the shared singleton).
- `names()` lists all registered tool names.
- `get(name)` returns a tool or raises `KeyError` for an unknown name.
- `register(tool)` adds a tool at runtime, keyed by `tool.name`.
- `for_category(category_key)` is the key method: it reads the category's `tool_bindings` and returns the corresponding `Tool` objects, silently skipping any binding name that isn't a known tool:
  ```python
  bindings = self._categories.get(category_key).tool_bindings
  return [self._tools[name] for name in bindings if name in self._tools]
  ```
  The `if name in self._tools` guard makes it robust to a category referencing a tool that hasn't been registered.

`get_tool_registry()` is the lazy-singleton accessor.

**Connects to** `BUILTIN_TOOLS` and `Tool` from `app.agents.tools` (the actual tool implementations); `CategoryRegistry`/`get_category_registry` from `category_registry.py` (source of the per-category bindings). Consumed by the agent engine to know which tools each category's node is allowed to call.

---

### How the whole area fits together

1. Settings pick a vendor. `registry.get_llm_provider(tier)` builds/caches the matching adapter (`Gemini/OpenAI/Claude/Fake`), all sharing one `AsyncRateLimiter` and one `TokenAccountant`.
2. Every adapter inherits rate limiting, timeout, bounded retry, token accounting, and JSON-structured output from `base.py`; it only implements the raw vendor call.
3. The engine renders a prompt for the current node via `PromptRegistry`, using the category's namespace/slots from `CategoryRegistry` and the gate thresholds from `ThresholdRegistry`, sends the resulting `ChatMessage` list through the active `LLMProvider`, and (for grounded answers) checks the result with `LLMVerifier` before delivery.
4. The registries make categories, prompts, thresholds, and tool bindings **data** — configurable in code and overridable from the database — so behavior changes without engine edits. The fakes make the entire pipeline runnable offline in tests.

---

I now have everything needed. Producing the walkthrough.

## Backend — RAG Pipeline

This section covers `backend/app/rag`, the code that turns a user's question into a small set of trustworthy, cited passages from the knowledge base (KB). RAG stands for **Retrieval-Augmented Generation**: instead of letting the language model answer from memory, we first *retrieve* real KB text and feed it in as grounding. The pipeline has two halves:

- **Ingestion (write path):** documents come in → parse to plain text → split into chunks → embed → store vectors in Chroma + rows in Postgres. Files: `parsers/`, `chunker.py`, `ingestion.py`, `vectorstore.py`.
- **Retrieval (read path):** a query comes in → search densely (vectors) and sparsely (keywords) in parallel → fuse the two lists → rerank → build a numbered context block and citation list. Files: `dense.py`, `sparse.py`, `fusion.py`, `reranker.py`, `retriever.py`, plus `vectorstore.py` again.

A design theme runs through every file: **depend on abstractions, not concretions.** Retrievers, the vector store, and the embedder are all defined as `Protocol`s or injected objects, so tests can pass in fakes and production can pass in the real Chroma/Postgres/OpenAI-backed implementations without any code change.

Two shared value objects (defined in `app/agents/state.py`, not in this folder, but used everywhere here) are worth knowing up front:

- `RetrievedChunk` — one candidate passage. Key fields: `chunk_id`, `doc_id`, `text`, a generic `score`, plus per-signal scores `dense_score`, `sparse_score`, `rerank_score`, and provenance (`source_uri`, `version`, `category_key`, `last_verified_at`, `metadata`).
- `Citation` — a slimmed-down provenance record (`chunk_id`, `doc_id`, `source_uri`, `version`, optional `quote`) attached to the final answer.

---

### `__init__.py`

**Purpose**: The package's public front door.

**How it works**: It re-exports only the three things callers outside the package should touch:

```python
from app.rag.retriever import HybridRetriever, RetrievalOutcome, Searcher
__all__ = ["HybridRetriever", "RetrievalOutcome", "Searcher"]
```

The one-line docstring names the four concerns of the package: "hybrid retrieval, fusion, reranking, chunking."

**Connects to**: Everything else in the folder is reachable through `HybridRetriever`, so the rest of the app imports from `app.rag` rather than reaching into individual modules.

---

### `parsers/` — turning raw bytes into plain text

Ingestion always starts from raw file bytes. The `parsers` sub-package's job is to produce clean, plain UTF-8 text no matter the original format, and to fail loudly (with a `ValidationError`) when a file is unsupported or empty. Every parser imports its heavy third-party library *lazily* (inside the function, not at module top), so the app can boot even if, say, `pypdf` isn't installed — you only pay the import cost when you actually parse that format.

#### `parsers/__init__.py`

**Purpose**: Dispatch a document to the right parser based on its content type or file extension.

**How it works**: It builds two lookup tables — one keyed by MIME content type, one by file extension:

```python
_EXTENSION_PARSERS = {
    ".pdf": parse_pdf, ".docx": parse_docx,
    ".html": parse_html, ".htm": parse_html,
    ".txt": parse_text, ".md": parse_text,
}
_CONTENT_TYPE_PARSERS = { "application/pdf": parse_pdf, ... }
```

`parse_document(data, *, filename, content_type)` is the single entry point. Its selection order matters:

1. **Content type wins first** — if `content_type` is present *and* known, use it. This is the most reliable signal because it usually comes from an HTTP upload header, not a user-chosen filename.
2. **Fall back to the file extension** — take `Path(filename).suffix.lower()` and look it up.
3. **Otherwise refuse** — `raise ValidationError(...)` naming both the filename and content type, so the failure is debuggable.

Note that `.txt` and `.md` both route to `parse_text`, and `.html`/`.htm` both route to `parse_html` — several extensions can share one parser.

**Connects to**: Called by `ingestion.py`'s `index_document`. Raises `ValidationError` from `app.core.exceptions`.

#### `parsers/pdf_parser.py`

**Purpose**: Extract text from a PDF.

**How it works**: Lazily imports `PdfReader` from `pypdf` (raising `ValidationError` if the library is missing). It wraps the raw bytes in `io.BytesIO(data)` so `pypdf` can read them as a file-like object, then extracts text page by page:

```python
pages = [page.extract_text() or "" for page in reader.pages]
```

The `or ""` guards against `extract_text()` returning `None` for a blank/graphics-only page. Pages are stripped and joined with blank lines (`"\n\n"`), keeping only non-empty pages. A crucial final check:

```python
if not text.strip():
    raise ValidationError("PDF contained no extractable text (it may be scanned).")
```

This catches the common real-world case of a **scanned PDF** (just images, no selectable text) and tells the operator why nothing came out, rather than silently indexing an empty document.

**Connects to**: Registered in `parsers/__init__.py` under `.pdf` / `application/pdf`.

#### `parsers/docx_parser.py`

**Purpose**: Extract text from a Word `.docx` file.

**How it works**: Lazily imports `docx` (python-docx) and opens the bytes with `docx.Document(io.BytesIO(data))`. It gathers text in two passes:

- **Paragraphs**: `[p.text for p in document.paragraphs if p.text and p.text.strip()]` — every non-blank paragraph.
- **Tables**: it walks `document.tables` → rows → cells, strips each cell, and joins a row's cells with `" | "`. This flattens tables into pipe-delimited lines so tabular content isn't lost during chunking/embedding.

Everything joins with newlines, and the same "empty means error" guard applies. Notice the difference from the PDF parser: paragraphs join with `"\n"` (single newline) here versus `"\n\n"` there — a stylistic choice, both are later collapsed by the chunker anyway.

**Connects to**: Registered under `.docx` and the long Office Open XML MIME type.

#### `parsers/html_text_parser.py`

**Purpose**: Extract text from HTML and from plain text/markdown — using only the Python standard library (no `beautifulsoup4` dependency).

**How it works**: It subclasses the stdlib `html.parser.HTMLParser` in a small `_TextExtractor`:

- `_SKIP_TAGS = {"script", "style", "head", "meta", "link"}` — content inside these is noise (JavaScript, CSS, metadata), not readable text.
- A depth counter `self._skip` is incremented on a start tag in that set and decremented on the matching end tag. Using a counter (not a boolean) correctly handles nesting.
- `handle_data` appends text *only* when `self._skip == 0` (we're not inside a skipped element) and the data isn't blank.
- The `text` property joins all collected fragments with single spaces.

Decoding is defensive:

```python
def _decode(data):
    try: return data.decode("utf-8")
    except UnicodeDecodeError: return data.decode("latin-1", errors="replace")
```

UTF-8 first (the modern default), with a `latin-1` fallback that can never fail because `errors="replace"` substitutes any undecodable byte. `parse_html` feeds the decoded string through the extractor; `parse_text` just decodes and returns it directly. Both raise `ValidationError` on empty output.

**Connects to**: Registered for `.html`/`.htm`/`text/html` (→ `parse_html`) and `.txt`/`.md`/`text/plain`/`text/markdown` (→ `parse_text`).

---

### `chunker.py`

**Purpose**: Split a long document into overlapping, word-bounded chunks small enough to embed and retrieve individually.

**Why chunk at all?** Embedding models and retrieval work best on passage-sized text. If you embedded a whole 40-page manual as one vector, a query would match the *average* of everything and cite nothing specific. Chunking lets retrieval pinpoint the exact paragraph that answers a question.

**How it works**: `chunk_text(text, *, chunk_size=800, overlap=120)` returns a list of strings.

1. **Validate arguments** — `chunk_size` must be positive, and `overlap` must be in `[0, chunk_size)`. The overlap-vs-size check prevents an infinite loop (if overlap ≥ size, the window could never advance).
2. **Normalize whitespace** — `normalized = " ".join(text.split())` collapses all runs of spaces, tabs, and newlines into single spaces. This is why the parsers didn't need to be fussy about their own spacing.
3. **Fast paths** — empty text returns `[]`; text already `<= chunk_size` returns a single-element list.
4. **The sliding window** — starting at `start = 0`, each iteration takes `end = min(start + chunk_size, length)`, then tries to snap `end` back to a **word boundary**:

   ```python
   if end < length:
       space = normalized.rfind(" ", start, end)
       if space > start:
           end = space
   ```

   `rfind` looks backward for the last space inside the window, so chunks don't cut a word in half. (The check is skipped when `end == length`, i.e. the final chunk.)
5. **Advance with overlap** — after appending the chunk, if we've reached the end we `break`; otherwise `start = max(0, end - overlap)`. Stepping back by `overlap` characters means consecutive chunks **share ~120 characters**. That overlap is deliberate: a sentence that straddles a chunk boundary still appears intact in one of the two neighboring chunks, so retrieval doesn't miss it.
6. A final comprehension drops any empty chunks.

The function is pure and deterministic — same input always yields the same chunks — which makes it trivially unit-testable and keeps re-ingestion stable.

**Connects to**: Called by `IngestionPipeline.index_text` in `ingestion.py`.

---

### `vectorstore.py`

**Purpose**: Wrap ChromaDB behind a clean async interface, and define the `VectorStore` `Protocol` that the rest of the RAG code depends on.

**How it works**:

- **`VectorHit` dataclass** — a frozen, immutable record of one search result: `id`, `score`, `document` (the chunk text), and `metadata`. Being `frozen=True` means results can't be accidentally mutated downstream.

- **`VectorStore` Protocol** — declares three async methods, `query`, `upsert`, and `delete`, all keyword-only. It's `@runtime_checkable`, so `isinstance(x, VectorStore)` works. Because it's a Protocol, `DenseRetriever` and `IngestionPipeline` type-hint against `VectorStore` and neither knows nor cares whether the real Chroma client or a test fake is behind it.

- **`ChromaVectorStore`** — the concrete implementation.
  - The constructor reads `CHROMA_HOST`, `CHROMA_PORT`, and `CHROMA_KB_COLLECTION` from settings but does **not** connect yet — `self._client = None`.
  - `_get_client()` lazily creates `chromadb.HttpClient(...)` on first use, converting a missing `chromadb` install into a domain `RetrievalError`. Lazy connection means importing this module has no side effects.
  - `_collection(name)` returns `get_or_create_collection(...)`, so a missing collection is created on demand rather than crashing.

  The interesting method is `query`:

  ```python
  res = coll.query(
      query_embeddings=[embedding], n_results=k,
      where=where or None,
      include=["documents", "metadatas", "distances"],
  )
  ```

  Chroma returns parallel lists nested one level deep (batched by query), so the code defensively unwraps the first row of each with patterns like `(res.get("ids") or [[]])[0]`. Then it converts each result into a `VectorHit`, and here is the single most important line in the file:

  ```python
  score=1.0 / (1.0 + float(distance)),  # distance -> similarity
  ```

  Chroma returns a **distance** (smaller = more similar), but the rest of the pipeline everywhere assumes **higher score = better**. This formula maps distance `0 → 1.0`, and larger distances toward `0`, giving a monotonic similarity in `(0, 1]`. Every ranking step downstream relies on this "bigger is better" convention.

  - **Threading**: Chroma's client is synchronous. To avoid blocking the async event loop, all three operations run their inner `_run()` inside `await asyncio.to_thread(...)`. This is the standard way to use a blocking library from async code.
  - **Error discipline** in `query`: a `RetrievalError` is re-raised as-is, but any other exception is logged and wrapped in `RetrievalError`, so callers only ever have to catch one exception type.

- **`ensure_collections()`** — a bootstrap helper that creates both canonical collections (`CHROMA_KB_COLLECTION` and `CHROMA_KB_PENDING_COLLECTION`) at startup. The pending collection holds chunks from documents that are still under review (not yet `published`).

- **`check_chroma()`** — a readiness probe for health checks. It calls `client.heartbeat()` and returns a bool; critically it **never raises** (catches everything and returns `False`), because a readiness endpoint should report "not ready," not crash.

**Connects to**: `DenseRetriever` (`dense.py`) calls `.query()`; `IngestionPipeline` (`ingestion.py`) calls `.upsert()`. Uses `get_settings`, `RetrievalError`, and `get_logger` from `app.core`.

---

### `ingestion.py`

**Purpose**: The write-path pipeline — take a document, and index its dense (vector) representation into Chroma. This is only the **dense half** of ingestion; the docstring is explicit that Postgres row persistence (`kb_documents` / `kb_chunks`, which powers sparse/BM25 search) is done by the ingestion **worker task** that calls this pipeline and writes back the returned chunks.

**How it works**:

- **`IngestionResult` dataclass** — the pipeline's return value: `doc_id`, the generated `chunk_ids`, the `chunks` text, `chunk_count`, and `embedding_model_id`. The worker uses these to write matching Postgres rows (so the same `chunk_ids` line up across Chroma and Postgres).

- **`IngestionPipeline.__init__`** — takes an `EmbeddingProvider` and a `VectorStore` (both abstractions) plus chunking defaults (`chunk_size=800`, `overlap=120`, matching `chunker.py`).

- **`index_text(...)`** — the core, with a deliberate step order:
  1. `chunk_text(...)` splits the document. If there are no chunks, it short-circuits and returns an empty result (still reporting the embedding model id).
  2. `embeddings = (await self._embedder.embed(chunks)).vectors` — **all chunks are embedded in one batched call**, which is far more efficient than embedding one at a time.
  3. `chunk_ids = [str(uuid.uuid4()) for _ in chunks]` — a fresh UUID per chunk; this id is shared as the Chroma vector id *and* the future Postgres `kb_chunks.id`.
  4. It builds a `metadatas` list, one dict per chunk, carrying the fields that dense retrieval later filters on: `org_id`, `doc_id`, `category_key`, `retrieval_namespace`, `doc_status` (defaulting to `PENDING_REVIEW`), `chunk_index`, `version`, `source_uri`, and `embedding_model_id`. **Every value is stringified/scalar** because Chroma metadata must be simple types, and these are exactly the keys the dense retriever's `where` filter reads back.
  5. `await self._vectorstore.upsert(...)` writes ids, vectors, documents, and metadata into the target `collection`. Using **upsert** (not insert) makes re-ingestion idempotent — re-indexing overwrites rather than duplicating.
  6. Returns a populated `IngestionResult`.

- **`index_document(...)`** — a thin wrapper: it calls `parse_document(data, filename=..., content_type=...)` to get plain text, then delegates to `index_text`. `**kwargs` forwards the org/doc/namespace/etc. arguments straight through.

**Note on `doc_status`**: new chunks default to `PENDING_REVIEW`. The dense retriever hard-filters to `"doc_status": "published"`, so freshly ingested content is invisible to end users until an approval step promotes it — a safety gate against unreviewed KB content leaking into answers.

**Connects to**: `chunk_text` (`chunker.py`), `parse_document` (`parsers/`), `VectorStore` (`vectorstore.py`), `EmbeddingProvider` (`app.providers.base`), `DocStatus` (`app.core.constants`).

---

### `dense.py`

**Purpose**: The **dense (semantic / vector) retriever** — the half of hybrid search that finds passages by *meaning*, not exact words.

**How it works**: `DenseRetriever` holds a `VectorStore` and an `EmbeddingProvider`.

- **`_where(filters)`** (static) translates the retriever's generic filter dict into a Chroma metadata filter, and it always starts with a **hard security/quality gate**:

  ```python
  where = {"doc_status": "published"}
  ```

  Only published chunks are ever searchable. It then copies through `org_id`, `retrieval_namespace`, and `category_key` when present (each stringified to match how ingestion wrote them). The `org_id` filter is the **tenant isolation boundary** — it prevents one organization's search from ever touching another org's vectors. There's also a naming-bridge: the conversation/ticket layer calls the field `category`, but chunks store `category_key`, so if `category_key` wasn't supplied it falls back to `filters["category"]`.

- **`search(query, *, filters, k)`**:
  1. Embed the query — `embedding = (await self._embedder.embed([query])).vectors[0]`. The same model that embedded the chunks during ingestion embeds the query, so they live in the same vector space.
  2. `hits = await self._store.query(embedding=..., k=k, where=self._where(filters))`.
  3. Convert each `VectorHit` into a `RetrievedChunk`, pulling provenance out of `hit.metadata`. Note it sets **both** `score=hit.score` and `dense_score=hit.score` — the generic `score` drives fusion ranking, while `dense_score` is preserved so the reranker (and debugging) can see the per-modality contribution.

**Connects to**: Implements the `Searcher` Protocol expected by `HybridRetriever`. Uses `VectorStore` (`vectorstore.py`), `EmbeddingProvider` (`app.providers.base`), and `RetrievedChunk`.

---

### `sparse.py`

**Purpose**: The **sparse (lexical / keyword) retriever** — the half of hybrid search that finds passages by exact term matching, using PostgreSQL full-text search (BM25-style `ts_rank`) over the `kb_chunks.text_fts` column.

**Why have both dense and sparse?** They fail in opposite ways. Dense search is great at synonyms and paraphrase but can miss a rare exact token (an error code, a product SKU). Sparse search nails exact keywords but is blind to meaning. Combining them (next file) gets the best of both.

**How it works**: `SparseRetriever` wraps a `KnowledgeRepository`.

- **`search(query, *, filters, k)`**:
  1. Requires `org_id` — if absent, returns `[]` immediately (again, tenant isolation: no org means no results).
  2. It normalizes `org_id` to a `uuid.UUID`, accepting either an actual UUID or a string: `org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id))`.
  3. Delegates to `self._kb.search_fts(...)`, passing namespace and category (again bridging `category_key` → `category`) and `limit=k`. The repo runs a Postgres query using `to_tsquery` + `ts_rank` against the GIN-indexed `text_fts` column, filtered to `DocStatus.PUBLISHED` — the same "published only" gate as the dense side.
  4. `search_fts` returns `(chunk, rank)` tuples. Each becomes a `RetrievedChunk` with `score=rank` **and** `sparse_score=rank` (mirroring the dense retriever's dual-assignment pattern). `last_verified_at` is converted from a datetime to an ISO string so the chunk stays JSON-serializable.

**Connects to**: Implements `Searcher`. Uses `KnowledgeRepository` (`app.repositories.kb_repo`, whose `search_fts` does the actual SQL) and `RetrievedChunk`.

---

### `fusion.py`

**Purpose**: Merge the dense and sparse result lists into one ranking using **Reciprocal Rank Fusion (RRF)**.

**The core idea**: Dense scores (a `1/(1+distance)` similarity) and sparse scores (Postgres `ts_rank`) are on completely different, incomparable scales — you can't just add them. RRF sidesteps this by ignoring the raw score magnitudes and using only each item's **rank position** in each list. A chunk that ranks near the top of *either* list, or moderately in *both*, floats up.

**How it works**: `reciprocal_rank_fusion(ranked_lists, *, k=60)`:

- Two dictionaries track state: `fused_scores` (chunk_id → accumulated score) and `merged` (chunk_id → the chunk object), the latter de-duplicating chunks that appear in both lists.
- For each list, for each chunk at position `rank` (0-based):

  ```python
  fused_scores[chunk.chunk_id] += 1.0 / (k + rank + 1)
  ```

  So rank 0 contributes `1/61`, rank 1 contributes `1/62`, and so on. The constant `k=60` (the standard RRF value) **dampens** the difference between top positions — it prevents the #1 result in one modality from utterly dominating, letting agreement across lists matter. A chunk found by *both* dense and sparse gets *two* additions, so cross-modality agreement is rewarded.
- **De-duplication with signal preservation**: the first time a chunk_id is seen it's stored; on a repeat, it's merged so both per-modality scores survive:

  ```python
  existing.model_copy(update={
      "dense_score": existing.dense_score or chunk.dense_score,
      "sparse_score": existing.sparse_score or chunk.sparse_score,
  })
  ```

  This is why the retrievers set only their own modality's field — after fusion, a chunk found by both carries both `dense_score` and `sparse_score`.
- Finally it writes the fused total into each chunk's generic `score` (via `model_copy`, keeping the chunks immutable) and sorts descending.

**Connects to**: Called by `HybridRetriever.retrieve` with `[dense_hits, sparse_hits]`. Consumes/produces `RetrievedChunk`; its output feeds the reranker.

---

### `reranker.py`

**Purpose**: Re-order the fused candidates using a richer relevance signal, then trim to the final top-K. The default `HeuristicReranker` is deliberately **model-free** — a fast, deterministic formula — with the explicit note that a heavy cross-encoder could be dropped in later without changing the interface.

**How it works**:

- **`_lexical_overlap(query, text)`** — a helper measuring word overlap. It lowercases and tokenizes both query and text, keeping only tokens longer than 2 characters (dropping noise words like "is", "to"), then returns the fraction of query words present in the text: `len(q & d) / len(q)`. Returns `0.0` if the query has no significant words. This rewards chunks that literally contain the user's key terms.

- **`HeuristicReranker`** is configured with two weights that sum to 1.0: `fused_weight=0.6`, `lexical_weight=0.4`.

- **`rerank(query, candidates, *, top_k)`**:
  1. Empty in → empty out.
  2. `max_fused = max(scores) or 1.0` — used to **normalize** fused scores into `[0, 1]` so they're comparable to the lexical fraction (the trailing `or 1.0` avoids divide-by-zero).
  3. For each chunk:
     - **Skip quarantined content**: `if chunk.metadata.get("is_quarantined"): continue`. This is a safety filter — chunks flagged bad (e.g. containing outdated or unsafe info) are dropped entirely, never reranked.
     - Compute `normalized_fused = chunk.score / max_fused` and `lexical = _lexical_overlap(...)`.
     - Read a `boost_factor` from metadata (default `1.0`) — a KB-relevance signal an admin can set to promote/demote a document.
     - Combine:

       ```python
       rerank_score = (0.6 * normalized_fused + 0.4 * lexical) * boost
       ```

       So the final score blends *retrieval strength* (fused) with *literal keyword match* (lexical), then scales by the editorial boost. The result is rounded and stored as `rerank_score` on a copy of the chunk.
  4. Sort by `rerank_score` descending and return `scored[:top_k]`.

**Connects to**: Instantiated by default inside `HybridRetriever`. Consumes the fused list from `fusion.py`; its `top_k` output is what actually reaches the language model. Relies on `metadata` keys (`is_quarantined`, `boost_factor`) that originate from `relevance_signals` (mentioned in the module docstring).

---

### `retriever.py`

**Purpose**: The orchestrator that ties the whole read-path together: run dense + sparse in parallel → fuse → rerank → assemble the numbered context block and citation list → and cache the result. This is the one class the rest of the app uses.

**How it works**:

**1. Module-level cache (perf optimization).**

```python
_RETRIEVAL_TTL = 300.0        # 5 minutes
_RETRIEVAL_MAX = 512          # max cached entries
_retrieval_cache: dict[tuple[str, str, str, str], tuple[float, "RetrievalOutcome"]] = {}
```

A process-local cache keyed by `(org_id, namespace, category, query)`. The reasoning (in the comment) is sound: the same filters + query deterministically produce the same candidates, so caching **never changes an answer**; a short 5-minute TTL keeps it fresh against KB edits; a 512-entry cap bounds memory.

**2. The `Searcher` Protocol.**

```python
@runtime_checkable
class Searcher(Protocol):
    async def search(self, query, *, filters, k) -> list[RetrievedChunk]: ...
```

Both `DenseRetriever` and `SparseRetriever` satisfy this shape structurally (no explicit inheritance needed). This is what lets `HybridRetriever` accept real backends or fakes interchangeably.

**3. `RetrievalOutcome`** — a Pydantic model bundling the full result: `candidates`, the `context` string, `citations`, and `max_relevance_score`. This mirrors the fields on `RetrievalState` in the agent graph.

**4. Two pure builder functions.**

```python
def build_context(candidates):
    return "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(candidates))
```

This produces the **numbered grounding block** fed to the model — each passage prefixed `[1]`, `[2]`, … The numbering is what lets the model cite sources as "[2]" in its answer, and it lines up positionally with:

```python
def build_citations(candidates):
    return [Citation(chunk_id=c.chunk_id, doc_id=c.doc_id,
                     source_uri=c.source_uri, version=c.version) for c in candidates]
```

Citation *i* corresponds to context marker `[i+1]` — same order, so the UI can turn "[2]" into a clickable source.

**5. `HybridRetriever`.**

- **Constructor** injects a dense `Searcher`, a sparse `Searcher`, an optional reranker (defaults to a fresh `HeuristicReranker()`), and two size knobs: `top_k=6` (final passages) and `candidate_k=20` (how many each modality fetches before fusion). Fetching 20 and keeping 6 gives fusion/rerank room to reorder.

- **`_safe_search(searcher, query, filters)`** — wraps one modality's search in try/except:

  ```python
  except Exception as exc:
      _logger.warning("%s failed: %s", type(searcher).__name__, exc)
      return []
  ```

  This is **graceful degradation**: if (say) Chroma is down, dense search returns `[]` but sparse still works, and the system answers from whatever half succeeded instead of erroring the whole turn.

- **`retrieve(...)`** — the main method, keyword-only args (`query`, `org_id`, optional `namespace`, `category`, `extra_filters`):
  1. Build the `filters` dict (`org_id`, `retrieval_namespace`, `category`), merging any `extra_filters`.
  2. **Cache lookup** — the key normalizes the query with `(query or "").strip().lower()`:

     ```python
     if not extra_filters and cache_key[3]:
         hit = _retrieval_cache.get(cache_key)
         if hit is not None and (time.monotonic() - hit[0]) < _RETRIEVAL_TTL:
             return hit[1]
     ```

     Two guards: the cache is used **only when there are no `extra_filters`** (the "common, filter-free path" — arbitrary extra filters would explode the key space and risk staleness), and **only for a non-empty query**. `time.monotonic()` is used (not wall-clock) so the TTL is immune to system clock changes.
  3. **Parallel search** — `asyncio.gather` runs both modalities concurrently:

     ```python
     dense_hits, sparse_hits = await asyncio.gather(
         self._safe_search(self._dense, query, filters),
         self._safe_search(self._sparse, query, filters),
     )
     ```

     Because each is wrapped in `_safe_search`, one failing doesn't cancel the other.
  4. **Fuse** → `reciprocal_rank_fusion([dense_hits, sparse_hits])`.
  5. **Rerank** → `await self._reranker.rerank(query, fused, top_k=self._top_k)`.
  6. **Score the turn** — `max_score = ranked[0].rerank_score or 0.0 if ranked else 0.0`. Because the reranker sorts descending, the top chunk's `rerank_score` is the best available; downstream agent logic uses this `max_relevance_score` to decide whether retrieval was good enough or the answer should abstain/escalate.
  7. Build the `RetrievalOutcome` (candidates + context + citations + max score).
  8. **Cache store** — under the same guards, with a simple bound:

     ```python
     if len(_retrieval_cache) >= _RETRIEVAL_MAX:
         _retrieval_cache.clear()
     _retrieval_cache[cache_key] = (time.monotonic(), outcome)
     ```

     The eviction is intentionally crude — when full, *clear everything* rather than track LRU. It's simple, and given the 5-minute TTL the cache is mostly short-lived anyway.

**Connects to**: This is the hub. It receives `Searcher` implementations from `dense.py` and `sparse.py`, calls `reciprocal_rank_fusion` (`fusion.py`) and `HeuristicReranker` (`reranker.py`), emits `RetrievedChunk`/`Citation` (`app.agents.state`), and its `RetrievalOutcome` populates the LangGraph `RetrievalState`. It is what `app/rag/__init__.py` re-exports as the package's public API.

---

### How the pieces fit end-to-end

**Ingestion (offline / write path):**
`bytes → parsers.parse_document → chunker.chunk_text → embedder.embed → ChromaVectorStore.upsert` (dense side), and the ingestion worker separately writes `kb_chunks` rows to Postgres (sparse side). Both sides key off the same `chunk_id` UUIDs, and both start life as `PENDING_REVIEW` until published.

**Retrieval (online / read path):**
`HybridRetriever.retrieve` → `[DenseRetriever.search (Chroma vectors), SparseRetriever.search (Postgres FTS)]` run in parallel → `reciprocal_rank_fusion` merges by rank → `HeuristicReranker` blends fused + lexical + boost and drops quarantined/unpublished-filtered chunks → `build_context` / `build_citations` produce the numbered grounding block and aligned citations → cached for 5 minutes → returned as a `RetrievalOutcome`.

The recurring engineering patterns to take away: **abstractions everywhere** (`Searcher`, `VectorStore`, `EmbeddingProvider` Protocols) for testability; **hard `org_id` + `doc_status="published"` gates** on both retrieval halves for tenant isolation and content safety; **graceful degradation** so one failing modality never sinks the turn; **immutable value objects** (`model_copy` instead of mutation); and **lazy imports** so optional heavy dependencies don't block startup.

Relevant file paths:
- `backend\app\rag\retriever.py`
- `...\backend\app\rag\dense.py`, `sparse.py`, `fusion.py`, `reranker.py`, `chunker.py`, `vectorstore.py`, `ingestion.py`, `__init__.py`
- `...\backend\app\rag\parsers\__init__.py`, `pdf_parser.py`, `docx_parser.py`, `html_text_parser.py`
- Referenced (outside this folder): `...\backend\app\agents\state.py` (`RetrievedChunk`, `Citation`), `...\backend\app\providers\base.py` (`EmbeddingProvider`), `...\backend\app\repositories\kb_repo.py` (`search_fts`)

---

## Backend — AI Engine core (LangGraph)

This is the "brain" of the helpdesk. Everything a user types flows through a **LangGraph** state machine: a set of *nodes* (steps) connected by *edges* (arrows). The graph carries a single shared object — `AgentState` — from node to node. Each node reads what it needs, writes its results back, and the *routing* functions decide which node runs next.

The files in this area split cleanly into responsibilities:

- **`state.py`** — defines the shared data object every node reads/writes.
- **`config_schema.py`** — carries the "live" service handles (DB, LLM, retriever) *around* the state, not inside it.
- **`graph.py`** — wires the nodes and edges together into the actual machine.
- **`routing.py`** — the decision logic that picks the next edge.
- **`confidence.py`** — pure math that scores how much we trust an answer.
- **`streaming.py`** — the typed events sent to the browser (typing dots, tokens, citations…).
- **`engine.py`** — the top-level object that compiles the graph once and runs/streams a turn.
- **`checkpointer.py`** — durable per-conversation memory so a thread can pause and resume.
- **`learning_graph.py`** — a separate, smaller graph that turns resolved tickets into KB articles.

Read them in that order and the whole engine clicks into place.

---

### `backend/app/agents/state.py`

**Purpose**: Defines `AgentState`, the *single, versioned contract* that flows through the entire graph — plus the typed sub-states, the list-merging reducer, and a helper that builds a fresh state at the start of each turn.

**How it works**

The file opens by explaining the core design rule in its docstring: the graph carries **one** state object, and *"Provider/service handles are NEVER stored here — they are injected via the LangGraph `config`"*. That separation is what keeps the state JSON-serializable so it can be saved to Postgres by the checkpointer. Remember that rule — `config_schema.py` is the other half of it.

**The reducer.** LangGraph needs to know *how* to combine writes to the same key when steps run. Most keys are "last write wins," but some are append-only logs. That is what `merge_lists` handles:

```python
def merge_lists(left, right):
    return (list(left) if left else []) + (list(right) if right else [])
```

It simply concatenates the old list with the new one. So when two nodes both append to `messages`, nothing is lost — they accumulate.

**Value objects.** Two small Pydantic models describe retrieval data:
- `RetrievedChunk` — one candidate document from the knowledge base, mirroring a `kb_chunks` row plus its various scores (`dense_score`, `sparse_score`, `rerank_score`, and a fused `score`).
- `Citation` — the trimmed-down provenance actually attached to a delivered answer (`chunk_id`, `doc_id`, `source_uri`, `version`, optional `quote`).

**Grouped sub-states.** Rather than dumping 50 loose fields into the state, related fields are bundled into six Pydantic models:
- `ConversationState` — what this turn is *about*: `category`, `intent`, `intent_confidence`, a `sensitivity_level`, a `control_intent` (`greeting | cancel | human_request | ...`), and slot-filling status (`required_slots`, `filled_slots`, `missing_slots`). Slots are the pieces of info the bot needs before it can help (e.g. "which laptop model?").
- `ExecutionContext` — per-run bookkeeping and *budgets*: `trace_id`, `turn_id`, `retry_budget` (default 1), and `max_clarifications` (default 2). These budgets are what stop the graph from looping forever.
- `MemoryState` — short-term memory: a rolling `summary`, a `recent_window`, durable `facts`, and `covered_through_turn`.
- `RetrievalState` — the query-planning inputs and results: `query`, `expanded_queries`, `candidates` (list of `RetrievedChunk`), the assembled `context` string, `max_relevance_score`, and a `sufficient` boolean.
- `ApprovalState` — human-in-the-loop status: `requires_approval`, `approved`, `awaiting_human`, `handoff_queue`.
- `StreamingState` — egress flags for the responder: `enabled`, `typing`, `cancelled`, `tokens_emitted`.

**The graph state itself.** `AgentState` is a `TypedDict` with `total=False` (every key is optional). It has three groups:

1. Identity/correlation: `thread_id`, `org_id`, `user_id`, `trace_id`, `turn_id`.
2. The three append-reduced channels, tagged with the reducer via `Annotated`:
   ```python
   messages: Annotated[list[dict[str, Any]], merge_lists]
   node_path: Annotated[list[str], merge_lists]
   audit_trail: Annotated[list[dict[str, Any]], merge_lists]
   ```
   `node_path` is especially handy — every node appends its own name, so after a run you can read exactly which path through the graph was taken.
3. The six sub-state objects, plus a big set of "hot" last-write-wins fields the routers read directly: `safety_verdict`, `injection_flag`, `cache_hit`, `decision`, `final_confidence`, `grounding_score`, `contradiction_flag`, `retry_count`, `clarification_rounds`, `draft_answer`, `citations`, `response_text`, `abstained`, and two that the routing logic leans on heavily:
   - `general_answer` — *"answer came from general LLM knowledge, not grounded KB"*.
   - `quick_replies` — *"suggested clickable replies for a clarification turn"*.

**`initial_state(...)`.** A keyword-only factory that seeds a brand-new state from an incoming user turn. It puts the user's text into `messages` as `[{"role": "user", "content": user_message}]`, constructs every sub-state fresh, mirrors the raw message into `normalized_query`/`redacted_query` (later nodes overwrite these), and zeroes out all the decision/output fields. Note `clarification_rounds` is passed in — it is *carried across turns* so a follow-up message knows the bot already asked once.

**Connects to**: `Decision` and `SensitivityLevel` come from `app.core.constants`; `utcnow` from `app.core.utils`. `AgentState` is the type parameter for the `StateGraph` in `graph.py`, the argument every routing selector inspects in `routing.py`, and the object `engine.py` seeds via `initial_state`. `merge_lists` is reused by `learning_graph.py` for *its* `node_path`.

---

### `backend/app/agents/config_schema.py`

**Purpose**: Defines `GraphDeps`, the bundle of live service handles (LLMs, retriever, DB gateways, registries) that nodes need — and passes it *around* the state through LangGraph's `config`, keeping the state itself serializable.

**How it works**

`GraphDeps` is a plain `@dataclass` where every field is typed `Any`. The docstring explains why: it is *"duck-typed so tests can inject fakes for every collaborator."* The fields are the entire toolbox the nodes reach for: two LLMs (`llm_large`, `llm_small` — big model for hard synthesis, small model for cheap classification), `embedder`, `verifier`, `retriever`, `memory`, `kb`, `tickets`, `notifications`, `analytics`, `feedback`, `audit`, `prompts`, `categories`, `thresholds`, `tools`, and a few optional ones defaulting to `None` (`users`, `conversations`, `redis`, `uploads` — the last being the *searcher over user-attached documents*).

Two helpers bracket the dataclass:

```python
def build_config(deps, *, thread_id):
    return {"configurable": {"deps": deps, "thread_id": thread_id}}
```
This is the shape LangGraph expects: anything under `configurable` is passed to every node at runtime. The `thread_id` here is what the checkpointer keys durable state on.

```python
def get_deps(config):
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    deps = configurable.get("deps")
    if not isinstance(deps, GraphDeps):
        raise RuntimeError("GraphDeps missing: pass build_config(deps, thread_id=...) ...")
    return deps
```
Every node calls `get_deps(config)` as its first line to unwrap the toolbox, and it fails loudly if someone forgot to pass it.

**Connects to**: `build_config` is called by `engine.py` (inside `_config`) to build the runnable config for each invocation; `get_deps` is called by every node (and by `learning_graph.py`'s nodes) to reach services. `thresholds` in this bundle is the `ThresholdSet` that `routing.py`/`confidence.py` consume.

---

### `backend/app/agents/graph.py`

**Purpose**: Assembles the main chat graph — registers the 13 nodes and wires them with fixed conditional edges — then compiles it (optionally with a checkpointer).

**How it works**

`build_graph()` creates a `StateGraph(AgentState)` (telling LangGraph the state type so it knows about the reducers), then registers all 13 nodes by name:

```python
graph.add_node("ingress_guard", ingress_guard)
graph.add_node("memory_manager", memory_manager)
... # intent_classifier, query_planner, rag_retriever, retrieval_gate,
    # solution_synthesizer, grounding_verifier, confidence_gate,
    # info_collector, ticket_creator, human_handoff, responder
```

The node *functions* live in `app.agents.nodes`; this file only wires them. Then the edges. Some are plain (`add_edge` — always go here next), others are conditional (`add_conditional_edges` — call a router function, which returns a string key that maps to the next node).

Walking the wiring top to bottom:

- `START -> ingress_guard`. Every turn begins at the safety/cache guard.
- **After ingress** (`route_after_ingress`): can jump straight to `responder` (blocked or cache hit or greeting/cancel), to `ticket_creator` (user asked for a human), or normally to `memory_manager`.
- **After memory** (`route_after_memory`): `"continue" -> intent_classifier` or `"end" -> END`. This node is *dual-mode* — the same node loads memory on the way in and persists it on the way out (more below).
- **After intent** (`route_after_intent`): to `responder` (smalltalk), `ticket_creator` (out of scope), `info_collector`, or the normal path `query_planner`.
- `query_planner -> rag_retriever -> retrieval_gate` (plain edges).
- **After the retrieval gate** (`route_after_retrieval_gate`): `solution_synthesizer` (good evidence), `info_collector` (ask a clarifying question once), or `ticket_creator`.
- **After synthesizer** (`route_after_synthesizer`): `ticket_creator` (the model abstained) or `grounding_verifier`.
- `grounding_verifier -> confidence_gate` (plain edge).
- **After the confidence gate** (`route_after_confidence`): `responder` (deliver), `info_collector` (clarify), `query_planner` (retry retrieval), or `ticket_creator` (escalate).
- **After info_collector** (`route_after_info_collector`): back to `rag_retriever` (got enough info), `ticket_creator` (out of clarification budget), or `responder` (ask the user; turn ends here awaiting a reply).
- `ticket_creator -> human_handoff -> responder`, and finally `responder -> memory_manager`.

That last edge is the clever part: **every path ends by returning to `memory_manager`**, which this time sees a `response_text` is set and routes to `END` (persisting memory on the way out). The module docstring summarizes the guarantee: *"every path reaches END via `responder -> memory_manager(persist) -> END`,"* and retry/clarification budgets mean *"there are no unbounded loops."*

`compile_graph(checkpointer=None)` just calls `.compile(checkpointer=...)`. Compiling is what turns the blueprint into something runnable.

**Connects to**: imports node functions from `app.agents.nodes`, all router functions from `routing`, and `AgentState` from `state`. `compile_graph` is called by `engine.py`. The `checkpointer` argument is fed a saver from `checkpointer.py`.

---

### `backend/app/agents/routing.py`

**Purpose**: Holds the deterministic decision logic — two "gate" policy functions used *inside* nodes, plus the edge-selector functions LangGraph calls to pick the next node. These functions **only read** state (no side effects).

**How it works**

**Gate 1 — `retrieval_is_sufficient`.** A one-liner policy: there must be candidates *and* the best score must clear the retrieval threshold.
```python
return bool(retrieval.candidates) and retrieval.max_relevance_score >= thresholds.retrieval
```

**Gate 2 — `decide_confidence`.** This is the central policy router (the "should we trust this answer?" brain). The docstring flags the key subtlety: *"Order matters — hallucination guard first."* The checks, in order:

- **(0) General answer bypass.** New and important:
  ```python
  if state.get("general_answer"):
      return Decision.DELIVER if answer_relevant else Decision.ESCALATE
  ```
  When the answer came from the LLM's *own* general knowledge (not the KB), it has no citations by design, so it must **skip** the grounding/citation guard below — otherwise a perfectly good general answer would always fail the "no citations = hallucination" test. It only needs to be *relevant*.
- **(a) Hard hallucination guard.** Overrides everything: if there's a contradiction, or the citations aren't valid, or the answer isn't relevant, `ESCALATE`. This runs *before* any confidence check, so a self-confident-but-wrong answer can't sneak through.
- **(b) Deliver.** If `final_confidence >= thresholds.deliver` AND `grounding_score >= thresholds.grounding_min`, `DELIVER`.
- **(c) Clarify.** If there are `missing_slots` *and* we haven't used up `max_clarifications`, `CLARIFY`.
- **(d) Retry.** If `retry_count < thresholds.retry_budget`, `RETRY_RETRIEVAL`.
- **(e) Otherwise `ESCALATE`.** The budgets in (c) and (d) guarantee this terminal fallback is eventually reached.

**The edge selectors.** These are what `graph.py`'s `add_conditional_edges` calls. Each returns a plain string:

- `route_after_ingress` — short-circuits to `responder` if `safety_verdict == "block"` or `cache_hit`; a `greeting`/`cancel` control intent also goes to `responder`; `human_request` goes to `ticket_creator`; else `memory_manager`.
- `route_after_memory` — the dual-mode trick spelled out: `"end" if state.get("response_text") is not None else "continue"`. First pass (no response yet) continues to classification; second pass (response set) ends.
- `route_after_intent` — handles `smalltalk` (-> responder) and `out_of_scope` (-> ticket_creator), but its default embodies the **answer-first** philosophy, per the comment: *"always retrieve (KB + uploaded files) before asking anything"* — so it returns `query_planner`. The bot tries to answer before it interrogates the user.
- `route_after_retrieval_gate` — if `sufficient`, synthesize. Otherwise the **clarify-once** rule:
  ```python
  if state["conversation"].missing_slots and state["clarification_rounds"] == 0:
      return "info_collector"
  return "solution_synthesizer"
  ```
  So when retrieval finds nothing, it asks for missing info *exactly once* (guided quick replies), and thereafter falls through to synthesize a best-effort general answer rather than looping or escalating.
- `route_after_synthesizer` — `ticket_creator` if the model `abstained`, else `grounding_verifier`.
- `route_after_confidence` — maps the `Decision` enum to a node: `DELIVER->responder`, `CLARIFY->info_collector`, `RETRY_RETRIEVAL->query_planner`, everything else (`ESCALATE`) `->ticket_creator`.
- `route_after_info_collector` — if slots are now filled, `rag_retriever` (retry with more info); if clarification budget is spent, `ticket_creator`; otherwise `responder` to actually ask the user, ending the turn to await their reply.

**Connects to**: reads `AgentState` fields written by the nodes; consumes `Decision` from `app.core.constants` and `ThresholdSet` from `app.registries.threshold_registry`. Every selector is referenced by name in `graph.py`. `decide_confidence`/`retrieval_is_sufficient` are called from within the gate nodes in `app.agents.nodes`, using scores produced by `confidence.py`.

---

### `backend/app/agents/confidence.py`

**Purpose**: A **pure, deterministic** scoring library (no I/O). It turns raw signals — retrieval scores, grounding, contradiction, citation markers — into numbers the confidence gate can threshold. Being pure makes it trivially unit-testable.

**How it works**

Helper `_clamp01` keeps every score in `[0, 1]`. A module-level regex `_CITATION_RE = re.compile(r"\[(\d+)\]")` finds citation markers like `[1]`, `[2]` in answer text.

`ConfidenceReport` is a frozen dataclass holding the full breakdown: `retrieval_confidence`, `grounding_score`, `citation_quality`, `hallucination_risk`, `answer_confidence`, `final_confidence`, and `contradiction`.

The individual scorers:
- `retrieval_confidence(max_relevance_score)` — just clamps the best retrieval score.
- `citation_quality(answer_text, num_citations)` — returns 0 if the answer is empty, has no `[n]` markers, or there are no citations; otherwise it computes the fraction of markers that point to a *valid* citation index (`1 <= m <= num_citations`). So an answer that cites `[5]` when only 3 sources exist is penalized.
- `hallucination_risk(grounding_score, contradiction)` — `1 - grounding`, plus a `0.5` penalty if a contradiction was detected, clamped.
- `answer_confidence(...)` — a **weighted blend**: if there's a contradiction it's instantly `0.0`; otherwise `0.20*intent + 0.30*retrieval + 0.35*grounding + 0.15*citation_quality`. Grounding carries the most weight; intent the least.

`evaluate(...)` is the single entry point the grounding verifier node calls. It defensively fills `None`s (`coalesce(intent_confidence, 0.5)`, grounding defaults to `0.0`), runs all the scorers, and returns a `ConfidenceReport` with everything rounded to 4 dp. Note `final_confidence` is currently set equal to `answer_confidence`.

**Connects to**: uses `coalesce` from `app.core.utils`. Its outputs (`final_confidence`, `grounding_score`, `contradiction`, plus citation validity/relevance) are exactly the arguments `decide_confidence` in `routing.py` expects — the confidence node bridges the two.

---

### `backend/app/agents/streaming.py`

**Purpose**: Defines the typed *wire events* the engine streams to the client, an SSE serializer, and a cooperative `CancellationToken`.

**How it works**

`StreamEventType` is a `StrEnum` of every event kind: `TYPING`, `TOKEN`, `PARTIAL`, `CITATIONS`, `QUICK_REPLIES`, `DECISION`, `TICKET`, `DONE`, `ERROR`, `CANCELLED`.

`StreamEvent` is a frozen dataclass — a `type`, a `data` dict, and an optional `index` (used to order streamed tokens). Its `to_sse()` renders the Server-Sent-Events wire format:
```python
return f"event: {self.type.value}\ndata: {json.dumps(payload)}\n\n"
```
That is exactly what a browser's `EventSource` expects, so the transport layer can forward these straight to the frontend.

Then a set of tiny factory functions build well-formed events so callers never hand-assemble dicts: `typing_event(on)`, `token_event(text, index)`, `citations_event(list)`, `quick_replies_event(options)` (carrying the clickable suggested replies), `decision_event(decision, confidence)`, `done_event(response_text, decision)`, `error_event(message)`, `cancelled_event()`.

`CancellationToken` is a minimal cooperative-cancel primitive: a private `_cancelled` flag, a `cancel()` mutator, and a `cancelled` property. The streaming loop checks it between tokens so a user hitting "stop" halts mid-stream cleanly.

**Connects to**: every factory here is imported and called by `engine.py`'s `astream`. `CancellationToken` is passed into `astream` by the transport/route layer. `quick_replies_event` pairs with the `quick_replies` field in `state.py`.

---

### `backend/app/agents/engine.py`

**Purpose**: `HelpdeskAIEngine` — the public façade. It compiles the graph **once**, then exposes `run` (one-shot) and `astream` (streamed). Its defining policy: **stream tokens only *after* the reliability gates have decided**, so the user never sees ungrounded text.

**How it works**

The constructor compiles the graph a single time and stashes a recursion limit:
```python
self._compiled = compile_graph(checkpointer or build_memory_checkpointer())
self._recursion_limit = recursion_limit
```
If no checkpointer is passed, it defaults to the in-memory one — so the engine always has durable-thread capability.

`_config(deps, thread_id)` builds the runnable config via `build_config` and injects `recursion_limit` (a safety cap on how many node-steps a single run may take).

`run(...)` is the workhorse. It seeds a fresh state with `initial_state(...)`, notably pulling the retry budget from the thresholds registry (`deps.thresholds.for_category(None).retry_budget`) and carrying `clarification_rounds` across turns, then awaits the graph:
```python
return await self._compiled.ainvoke(seed, self._config(deps, thread_id))
```
That returns the *final* `AgentState` after the graph has run to a decision.

`astream(...)` layers streaming on top of `run` — and this is where the "gates first, tokens later" policy is visible:

1. `yield typing_event(True)` — show typing dots immediately.
2. `await self.run(..., streaming=True)` inside a try/except. If the graph raises, log it and `yield error_event(str(exc))`, then return. **The whole graph runs to completion first** — no tokens are emitted until the final, gated answer exists.
3. `yield typing_event(False)` — stop the dots.
4. Stream the final text word-by-word, checking cancellation each step:
   ```python
   for index, word in enumerate(text.split(" ")):
       if cancel_token is not None and cancel_token.cancelled:
           yield cancelled_event()
           return
       yield token_event(word + " ", index)
   ```
   This *simulates* token streaming by splitting the already-decided answer, so the UX feels live while the safety guarantee holds.
5. After the text: if there are `citations`, emit them (`[c.model_dump() for c in citations]`); if there are `quick_replies`, emit those; then a `decision_event` (with `final_confidence`) and finally a `done_event`.

At module level, `get_ai_engine()` is a lazy singleton so the app compiles the graph once per process:
```python
def get_ai_engine():
    global _engine
    if _engine is None:
        _engine = HelpdeskAIEngine()
    return _engine
```

**Connects to**: pulls the graph from `graph.compile_graph`, the default saver from `checkpointer.build_memory_checkpointer`, config helpers + `GraphDeps` from `config_schema`, the seed from `state.initial_state`, and *all* event factories + `CancellationToken` from `streaming`. Logging via `app.core.logging`. This is the object the API/route layer calls to answer a chat message.

---

### `backend/app/agents/checkpointer.py`

**Purpose**: Chooses the LangGraph checkpointer (durable per-thread state store). In-memory for dev/tests, Postgres-backed for production. This is what makes `human_handoff` interrupts and reconnect/resume on the same `thread_id` possible.

**How it works**

`build_memory_checkpointer()` lazily imports and returns a `MemorySaver` — RAM-only, perfect for tests. The imports are inside the functions on purpose (per the docstring, *"so this module loads without LangGraph installed"*).

`build_postgres_checkpointer(dsn=None)` resolves a DSN — either the one passed in, or the app's sync DSN with the SQLAlchemy driver prefix stripped:
```python
resolved = dsn or get_settings().sqlalchemy_sync_dsn.replace(
    "postgresql+psycopg://", "postgresql://")
```
It then tries to build a `PostgresSaver.from_conn_string(resolved)`, and if the Postgres extra isn't installed it logs a warning and gracefully falls back to the in-memory saver. The docstring notes the operational detail: you call `.setup()` once, then keep the returned context manager open for the app's lifetime.

**Why a checkpointer matters here**: because the graph *saves* state keyed by `thread_id` after each step, a turn that ends at `info_collector -> responder` (asking the user a question) can pause, and the next user message resumes the same thread with `clarification_rounds` and memory intact. That is the durable-conversation backbone.

**Connects to**: reads settings via `app.core.config.get_settings`, logs via `app.core.logging`. `build_memory_checkpointer` is the default used by `engine.py`. The saver it returns is handed to `compile_graph` in `graph.py`. The `thread_id` it keys on is the same one placed in `config` by `config_schema.build_config`.

---

### `backend/app/agents/learning_graph.py`

**Purpose**: A **separate, smaller** async graph — the "feedback learner." It runs out-of-band (from a worker) when a ticket is resolved, a user gives feedback, or an admin uploads a doc, turning that raw resolution text into a reusable KB article: `draft -> approval_gate -> kb_upsert`.

**How it works**

It has its own state, `LearningState` (`TypedDict, total=False`), with a `node_path` that reuses `merge_lists` from `state.py`. Fields track the pipeline: `trigger`, `org_id`, `source_text`, `category`, `draft`, `approved`, `chunk_count`, `status`.

Three async nodes, each taking `(state, config)` and returning a partial update dict — the same node signature as the main graph, and each reaches its dependencies via the same `get_deps(config)`:

- `draft_node` — builds two `ChatMessage`s (a system prompt asking for a concise reusable KB article with a title and steps, plus the raw `source_text`) and calls the large model: `result = await deps.llm_large.generate(messages)`. If generation throws, it falls back to the raw source text so the pipeline never dies. Returns the draft and `status=DRAFTED`.
- `approval_gate` — a placeholder policy: approve iff the draft is non-empty (`bool(state.get("draft", "").strip())`), setting `status` to `APPROVED` or `REJECTED`.
- `kb_upsert` — chunks the approved draft with `chunk_text` and reports `chunk_count`. Per the docstring, the *actual* vector upsert is done later by the ingestion worker/Chroma; this node only prepares and records the chunks.

Wiring mirrors the main graph's style: `START -> draft -> approval_gate`, then a conditional split via `_route_after_approval` (`"upsert" if approved else "end"`), and `kb_upsert -> END`. `build_learning_graph()` returns the uncompiled graph; `compile_learning_graph()` compiles it (note: **no checkpointer** — this is a fire-and-forget batch job, not a durable conversation).

**Connects to**: reuses `merge_lists` from `state.py` and `get_deps` from `config_schema.py` (so it accepts the same `GraphDeps` bundle). Uses `LearningStatus` from `app.core.constants`, `chunk_text` from `app.rag.chunker`, `ChatMessage` from `app.providers.base`, and `llm_large` from the injected deps. It closes the loop with the main graph: articles it produces become the KB that `rag_retriever` searches on future turns.

---

### The full request flow, end to end

Putting it all together, here is what happens when a user sends "My VPN won't connect":

1. The route layer calls `get_ai_engine().astream(...)`. The engine emits **typing on**, then runs the compiled graph to completion via `run` (seeded by `initial_state`, with `GraphDeps` and `thread_id` in the config).
2. `START -> ingress_guard`: safety + cache + injection checks. `route_after_ingress` sees no block/cache/greeting, so `-> memory_manager`.
3. `memory_manager` (load mode): pulls summary/facts. `route_after_memory` sees no `response_text` yet, so `-> intent_classifier`.
4. `intent_classifier`: sets category/intent. `route_after_intent` follows **answer-first** and returns `query_planner`.
5. `query_planner -> rag_retriever -> retrieval_gate`: the query is planned, the KB (and any uploaded files) searched, and `retrieval_is_sufficient` records whether evidence cleared the threshold.
6. `route_after_retrieval_gate`: if evidence is good `-> solution_synthesizer`; if it's thin and info is missing and we haven't asked yet `-> info_collector` (**clarify once**); otherwise still `-> solution_synthesizer` for a best-effort answer.
7. `solution_synthesizer` drafts an answer. If it abstained `-> ticket_creator`; else `-> grounding_verifier`.
8. `grounding_verifier` calls `confidence.evaluate(...)`, then `confidence_gate` calls `routing.decide_confidence(...)`. The `general_answer` bypass, the hallucination guard, and the deliver/clarify/retry/escalate ladder pick a `Decision`.
9. `route_after_confidence` maps that decision to `responder` (deliver), `info_collector` (clarify), `query_planner` (retry — bounded by `retry_budget`), or `ticket_creator` (escalate — which flows `ticket_creator -> human_handoff -> responder`).
10. `responder` sets `response_text` (and maybe `citations`/`quick_replies`), then `responder -> memory_manager`. This time `route_after_memory` sees `response_text` and routes `-> END`, persisting memory on the way out.
11. Back in `astream`, the engine now has the *final, gated* state: it turns off typing, streams the answer word-by-word (checking the `CancellationToken`), then emits citations, quick replies, the decision, and done.

Every loop (retry, clarify) is capped by the budgets in `ExecutionContext` and `ThresholdSet`, so the graph is guaranteed to terminate in **deliver** or **handoff** — and the checkpointer keeps the whole thread resumable if it pauses to ask the user something.

---

## Backend — AI Engine nodes

These files live in `backend/app/agents/nodes/` and are the individual "workers" of the chat brain. The system is built with **LangGraph**, a library that lets you describe an AI workflow as a *graph*: each node is a small async function that receives the current `AgentState` (a big shared dictionary describing the conversation), does one job, and returns a small dictionary of updates that LangGraph merges back into the state. The *edges* between nodes decide who runs next.

Two ideas make this whole area easy to read once you know them:

1. **Every node has the same shape.** Its signature is `async def node(state: AgentState, config: RunnableConfig) -> dict[str, Any]`. It reads from `state`, reaches its external services through `deps = get_deps(config)` (defined in `config_schema.py`), and returns only the fields it wants to change. It never mutates `state` in place.
2. **Nodes never decide routing directly.** They just write facts into the state (like `safety_verdict`, `retrieval.sufficient`, or `decision`). Separate *selector* functions in `routing.py` read those facts and pick the next node. This keeps side-effects (nodes) and control-flow (routing) cleanly separated.

Before the file-by-file walkthrough, here is the order they run in, taken from `graph.py` and `routing.py`.

### The order of execution

The graph is wired in `backend/app/agents/graph.py`. The happy path and its branches look like this:

```
START
  -> ingress_guard          (safety, redaction, cache, control-intent)
       |-- blocked / cache hit / greeting / cancel --> responder
       |-- "human_request"                          --> ticket_creator
       '-- otherwise                                --> memory_manager (LOAD)
  -> memory_manager (load)  --> intent_classifier
  -> intent_classifier      (category + slot extraction)
       |-- smalltalk        --> responder
       |-- out_of_scope     --> ticket_creator
       '-- otherwise        --> query_planner
  -> query_planner          --> rag_retriever
  -> rag_retriever          (KB + uploaded files) --> retrieval_gate
  -> retrieval_gate         (evidence sufficient?)
       |-- sufficient                        --> solution_synthesizer
       |-- thin + missing info (first round)  --> info_collector
       '-- thin (otherwise)                  --> solution_synthesizer
  -> solution_synthesizer   (grounded, else general answer)
       |-- abstained --> ticket_creator
       '-- answered  --> grounding_verifier
  -> grounding_verifier     --> confidence_gate
  -> confidence_gate        (the central decision)
       |-- DELIVER          --> responder
       |-- CLARIFY          --> info_collector
       |-- RETRY_RETRIEVAL  --> query_planner   (loops, budget-limited)
       '-- ESCALATE         --> ticket_creator
  info_collector
       |-- slots now filled           --> rag_retriever
       |-- out of clarification budget --> ticket_creator
       '-- ask the user               --> responder (turn ends, awaits reply)
  ticket_creator --> human_handoff --> responder
  responder      --> memory_manager (PERSIST) --> END
```

Notice `memory_manager` appears twice on every full run — once near the start (LOAD mode) and once at the very end (PERSIST mode). And `responder` is the single exit: no matter what happened, the last thing before saving memory is composing one user-facing reply.

Now, each file.

---

### `__init__.py` — the node registry

**Purpose:** Import every node function and expose them together, plus document how these 13 files map onto the abstract "functional node list" from the architecture doc.

**How it works:** The top docstring is the Rosetta Stone between the design's conceptual roles and the actual files. For example it tells you the conceptual *"Confidence Eval"* role is split across two real files: `grounding_verifier` + `confidence_gate`, and the *"Retriever"* role is `query_planner` + `rag_retriever`. After importing all 13 functions, it builds a dictionary:

```python
NODES = {
    "ingress_guard": ingress_guard,
    "memory_manager": memory_manager,
    ...
    "responder": responder,
}
```

This dict is a convenient name-to-function lookup. The last line, `__all__ = ["NODES", *sorted(NODES)]`, makes both the dict and every individual node importable via `from app.agents.nodes import ...`.

**Connects to:** `graph.py` imports the individual functions from here to register them as graph nodes. The docstring's mapping mirrors the roles referenced throughout `routing.py`.

---

### `ingress_guard.py` — the deterministic front door

**Purpose:** The very first node. A *no-LLM* (pure Python) safety and pre-processing gate: it normalizes the message, redacts personal data, detects prompt-injection attacks, spots simple "control" phrases (greetings, "talk to a human", "cancel"), and checks a cache for an already-computed answer. Being deterministic means it is fast, free, and predictable — exactly what you want guarding the entrance.

**How it works:**

It compiles a few regular expressions once at module load. `_EMAIL_RE` matches email addresses, `_DIGITS_RE` matches any run of 6+ digits (phone numbers, IDs), and `_INJECTION_RE` matches known jailbreak phrasings:

```python
_INJECTION_RE = re.compile(
    r"(ignore (all |the )?(previous|prior) instructions|disregard (the )?system|"
    r"reveal your (system )?prompt|you are now)",
    re.IGNORECASE,
)
```

`_redact(text)` runs both PII regexes, replacing emails with `[email]` and long digit runs with `[number]`. This redacted copy is what gets stored/logged, so raw PII never leaks into transcripts.

`_control_intent(text)` checks for canned conversational phrases. If the whole message is just `"hi"`/`"thanks"` etc. it returns `"greeting"`; phrases like "speak to a human" return `"human_request"`; "cancel"/"stop" return `"cancel"`; otherwise `None`. These short-circuit the expensive AI pipeline for trivial turns.

In the node itself, `normalized = " ".join(raw.split())` collapses runs of whitespace into single spaces — a cheap way to standardize the input. Then it computes a `query_hash` (a SHA-256 of the lowercased normalized text) which is used as a cache key. It sets `safety_verdict` to `"block"` if injection was detected, else `"ok"`, and records everything in an `audit_trail` entry.

The cache lookup is the interesting best-effort part:

```python
if deps.redis is not None and not injection and control is None:
    try:
        cached = await deps.redis.get(f"answer:{state['org_id']}:{query_hash}")
        if cached:
            updates["cache_hit"] = True
            updates["cached_answer"] = cached
    except Exception:
        pass
```

It only bothers with the cache for *real* questions (not injections, not greetings), it namespaces by `org_id` so tenants can't see each other's answers, and it swallows any Redis error — the cache is a nice-to-have, never a hard dependency.

**Connects to:** Its outputs are read by `routing.route_after_ingress`, which sends `"block"`/`cache_hit`/greeting/cancel straight to `responder`, `"human_request"` to `ticket_creator`, and everything else onward to `memory_manager`. The `cached_answer` field is later read by `responder._compose`. It gets its Redis handle from `GraphDeps` (`config_schema.py`).

---

### `memory_manager.py` — dual-mode load-then-persist

**Purpose:** Manage long-term conversation memory. This one node does *two different jobs* depending on where we are in the run: near the start it **loads** prior memory (rolling window, summary, known facts); at the very end it **persists** the updated memory. One file, two modes, chosen by a simple state check.

**How it works:**

A small helper `_uuid(value)` safely converts the string IDs in state into real `UUID` objects, returning `None` on bad input instead of throwing. The node converts `user_id`, `thread_id` (as `conversation_id`) and `org_id`.

The mode switch is this single line:

```python
if state.get("response_text") is not None:
```

`response_text` is only set by `responder`, which runs at the very end. So if it's present, we must be on the second visit — **persist mode**. It calls `deps.memory.persist_turn(...)`, passing the current `state["memory"]`, and returns the updated memory object. Its `node_path` marker is `"memory_manager:persist"` so audit logs can tell the two visits apart.

Otherwise it's the first visit — **load mode** — and it calls `deps.memory.load_state(...)` to hydrate `memory` before the classifier runs.

Both modes are guarded: if `deps.memory` is missing or any ID failed to parse, it returns just the `node_path` marker and moves on. Both wrap the service call in `try/except` and, on error, record the exception into `audit_trail` rather than crashing — memory maintenance is explicitly "best-effort".

**Connects to:** `routing.route_after_memory` reads `response_text` too: if present it routes to `END` (we're done), otherwise to `intent_classifier`. Its `deps.memory` service comes from `GraphDeps`. The `state["memory"]` object it fills is later read by `intent_classifier` and `query_planner` (they use `memory.summary`).

---

### `intent_classifier.py` — routing category + slot extraction

**Purpose:** The "Router". Uses the small, cheap LLM to classify the message (category, intent, confidence, sensitivity) and — importantly — to perform **slot extraction**: pulling out the specific intake fields (like an error message or affected app) that the user already stated. Filling slots is what lets a clarified conversation eventually move forward instead of asking questions forever.

**How it works:**

`IntentResult` is a Pydantic model describing the classification the LLM must return, with safe defaults (`category="application_error"`, `intent_confidence=0.5`, etc.). Using a Pydantic model with `generate_structured` forces the LLM's output into a validated shape.

The clever part is `_extract_slots`. Slots are the required pieces of info for a given category. This helper decides which ones still need extracting:

```python
to_find = [s for s in required if not prior_filled.get(s) and s != "issue_type"]
if not to_find:
    return {}
```

It skips slots that are already filled (no wasted LLM round-trip) and deliberately skips `"issue_type"` — that one is a *guided-choice* slot answered by clicking a quick-reply button, not something to mine from free text. Leaving it unfilled is intentional: it lets a vague first message trigger the guided clarification with buttons.

It then builds a throwaway Pydantic model *on the fly* with `create_model`, one nullable string field per slot to find:

```python
model = create_model("SlotExtraction", **{s: (str | None, None) for s in to_find})
```

The system prompt is strict — *"Fill a field ONLY if the user explicitly provided that information; otherwise leave it null. Never guess or infer."* — because inventing slot values would be worse than leaving them empty. The whole thing is wrapped in `try/except` returning `{}` on failure ("missing stays missing"), and the final comprehension keeps only truthy, stripped values.

In the main function: it renders the `"router"` prompt (injecting the available `categories` and the conversation `summary`), calls `deps.llm_small.generate_structured`, and falls back to a default `IntentResult()` if that fails. Then it computes the slot bookkeeping:

```python
required = deps.categories.required_slots(result.category)
prior_filled = dict(state["conversation"].filled_slots)
newly = await _extract_slots(deps, required, prior_filled, state["normalized_query"])
filled = {**prior_filled, **newly}
missing = [slot for slot in required if not filled.get(slot)]
```

So `filled` merges what we already knew with what we just extracted, and `missing` is whatever's still blank. It validates `sensitivity_level` against the known enum values (`SensitivityLevel`), defaulting to `LOW` if the LLM returned something unexpected. Finally it writes all of this into an updated `conversation` object via `model_copy(update={...})` — the immutable-update pattern used throughout — and surfaces `intent_confidence` to the top level of state for the confidence engine.

**Connects to:** `routing.route_after_intent` reads `control_intent` to optionally divert to `responder`/`ticket_creator`, otherwise sends to `query_planner`. The `missing_slots`/`filled_slots` it writes drive `info_collector` and the CLARIFY logic in `routing.decide_confidence`. `required_slots` and category info come from `deps.categories`; the LLM from `deps.llm_small`; prompts from `deps.prompts`.

---

### `query_planner.py` — rewrite the search query

**Purpose:** First half of the "Retriever". Takes the raw user message and rewrites it into a clean, standalone search query — resolving coreferences (turning "it won't open" into "Outlook won't open" using conversation context) — and sets up the retrieval namespace and filters for the given category.

**How it works:**

It looks up the category's dedicated retrieval namespace (`deps.categories.get(category).retrieval_namespace`) — different topics search different corpora. `fallback = state["normalized_query"]` is the safety net: if anything goes wrong, we just search with the original text.

It renders the `"retriever"` prompt with the category, memory summary, and input, then calls the *small* LLM to produce a rewrite. The post-processing is worth noting:

```python
lines = [ln.strip() for ln in result.text.splitlines() if ln.strip()]
planned = (lines[0] if lines else fallback) or fallback
```

It keeps only the first non-empty line. The comment explains why: the rewrite must be a single concise query, and any stray preamble ("Sure, here's the query:") or blank line "would pollute lexical reranking." The whole LLM call is wrapped in `try/except` that degrades to `fallback`.

Finally it writes the plan into the `retrieval` sub-state:

```python
retrieval = state["retrieval"].model_copy(update={
    "query": planned,
    "namespace": namespace,
    "filters": {"org_id": state["org_id"], "retrieval_namespace": namespace, "category": category},
})
```

The `org_id` in `filters` is the tenant-isolation boundary for search.

**Connects to:** Unconditionally followed by `rag_retriever` (a plain edge in `graph.py`), which reads the `query`, `namespace`, and category it set. It's also the *retry* target: when `confidence_gate` decides `RETRY_RETRIEVAL`, the graph loops back here to re-plan. Uses `deps.llm_small`, `deps.prompts`, `deps.categories`.

---

### `rag_retriever.py` — hybrid retrieval + merge uploaded files

**Purpose:** Second half of the "Retriever". Actually fetches candidate passages (hybrid vector + keyword search), then **merges in results from user-uploaded documents** ("Document Search") so the assistant can answer from files an admin/engineer attached — not just the curated knowledge base. Produces the context string and citations the answer will be built from.

**How it works:**

It reads the planned query (`retrieval.query or state["normalized_query"]`) and calls the retriever service:

```python
outcome = await deps.retriever.retrieve(query=query, org_id=..., namespace=..., category=...)
```

If that throws, retrieval failure "degrades to escalation" — it returns an `error` and no candidates, which downstream will turn into a ticket.

The upload-merge is the distinctive feature:

```python
uploads = getattr(deps, "uploads", None)
if uploads is not None:
    extra = await uploads.search(state["org_id"], query)
    if extra:
        candidates = (candidates + list(extra))
        candidates.sort(key=lambda c: (c.rerank_score or c.score or 0.0), reverse=True)
        candidates = candidates[: max(len(outcome.candidates), 6)]
        max_relevance = max(max_relevance, max((c.rerank_score or c.score or 0.0) for c in extra))
```

Step by step: it uses `getattr` so a deployment without the uploads feature simply skips it. It searches uploaded docs scoped to the org, concatenates those hits with the KB candidates, re-sorts the combined list by best available score (`rerank_score`, else `score`, else 0), and trims to a sensible cap (at least 6, or however many the KB returned). Crucially it also raises `max_relevance` to account for a strong uploaded-file match — otherwise a great answer sitting in an uploaded PDF might not clear the sufficiency gate.

Then it builds the final artifacts:

```python
from app.rag.retriever import build_citations, build_context
updated = retrieval.model_copy(update={
    "candidates": candidates,
    "context": build_context(candidates),
    "max_relevance_score": max_relevance,
})
return {"retrieval": updated, "citations": build_citations(candidates), ...}
```

`build_context` stitches the passages into the text block the LLM will read; `build_citations` creates the `[1]`, `[2]` reference list. Note the import is local (inside the function) rather than at module top.

**Connects to:** Always followed by `retrieval_gate`. Its `max_relevance_score` is the key number the retrieval gate and confidence engine judge. Its `citations` feed `confidence_gate` (citation quality) and `context` feeds `solution_synthesizer`. It's also re-entered from `info_collector` once slots are filled. Uses `deps.retriever`, optional `deps.uploads`, and helpers in `app/rag/retriever.py`.

---

### `retrieval_gate.py` — reliability gate #1 (is the evidence good enough?)

**Purpose:** A tiny deterministic gate that answers one yes/no question: *did we retrieve strong-enough evidence to try answering?* This is the first of two reliability checks and prevents the system from confidently answering from thin air.

**How it works:**

It fetches the thresholds appropriate to this category *and* sensitivity level — a "password reset" question and a "security incident" can demand different bars:

```python
thresholds = deps.thresholds.for_category(conversation.category, conversation.sensitivity_level)
sufficient = retrieval_is_sufficient(state, thresholds)
retrieval = state["retrieval"].model_copy(update={"sufficient": sufficient})
```

The actual logic lives in `routing.retrieval_is_sufficient`: `bool(retrieval.candidates) and retrieval.max_relevance_score >= thresholds.retrieval` — i.e. there must be at least one candidate *and* the top score must clear the category's retrieval threshold. It records the verdict and the `max_score` into the audit trail.

**Connects to:** `routing.route_after_retrieval_gate` reads `retrieval.sufficient`: if sufficient, go to `solution_synthesizer`; if thin AND there are missing slots AND it's the first clarification round, divert to `info_collector` to ask the user once; otherwise still go to `solution_synthesizer` (which will then attempt a general answer). Thresholds come from `deps.thresholds`.

---

### `info_collector.py` — the clarifier with quick replies

**Purpose:** The "Clarifier". When we need more information, this node composes a single follow-up question and, importantly, attaches **quick-reply buttons** — curated clickable choices per category — so the user gets guided troubleshooting instead of a blank text box.

**How it works:**

`_QUICK_REPLIES` is a hand-written dictionary mapping category -> list of button labels, e.g.:

```python
"wifi": ["Can't connect at all", "Keeps dropping", "Connected but no internet", "Very slow", "Other"],
```

`_GENERIC_QUICK` is the fallback for unknown categories, and `_quick_replies(category)` just does the lookup with that fallback. The comment stresses these are hard-coded on purpose: "No extra LLM call — keeps the turn fast and within provider quota."

The node bumps the clarification counter (`rounds = state["clarification_rounds"] + 1`), then renders the `"clarification"` prompt — passing the category, the still-missing slots, the already-filled slots, and the user input — and asks the small LLM to phrase a natural question. If the LLM call fails, it falls back to a templated question that literally lists the missing slots.

It returns:

```python
return {
    "clarification_rounds": rounds,
    "draft_answer": question,
    "decision": Decision.CLARIFY,
    "quick_replies": _quick_replies(conversation.category),
    "node_path": ["info_collector"],
}
```

Note it stashes the question in `draft_answer` and sets `decision = CLARIFY`; the `responder` will surface that text and the `quick_replies` to the UI.

**Connects to:** `routing.route_after_info_collector` decides what happens after asking: if `missing_slots` is now empty it goes back to `rag_retriever` (retry the answer with the new info); if the clarification budget (`execution.max_clarifications`) is exhausted it gives up to `ticket_creator`; otherwise it goes to `responder`, ending the turn to wait for the user's reply. It is reached from three places: the retrieval gate, the confidence gate (CLARIFY), and via the intent router. Uses `deps.prompts`, `deps.llm_small`.

---

### `solution_synthesizer.py` — grounded answer, then general fallback (two-stage)

**Purpose:** The "Knowledge Answer" node — where the actual answer is written by the *large* LLM. Its two-stage design is the heart of the "always try to help" philosophy: **(1)** if retrieval is strong, answer strictly from the sources with citations; **(2)** if not (or if the grounded attempt admits it can't answer), fall back to a clearly-flagged general answer rather than immediately escalating. Only a genuine LLM failure or an empty result makes it give up.

**How it works:**

The helper `_is_abstain(text)` detects the model signalling "I can't answer this from the sources" — it checks whether the word `ABSTAIN` appears in the first 80 characters (the prompt instructs the model to lead with that keyword when the sources don't cover the question). Empty text also counts as abstaining.

**Stage 1 — grounded (only if `retrieval.sufficient`):**

```python
if retrieval.sufficient:
    messages = deps.prompts.render("knowledge", input=query, context=retrieval.context or "")
    try:
        text = (await deps.llm_large.generate(messages)).text.strip()
    except Exception as exc:
        if retrieval.candidates and retrieval.max_relevance_score >= 0.6:
            top = retrieval.candidates[0].text.strip()
            return {"draft_answer": ("Here's the most relevant information ... [1]:\n\n"
                    f"{top}\n\n> **Note:** Served directly from the matching source."),
                    "claims": [retrieval.candidates[0].text], "abstained": False, ...}
        text = None
```

The `"knowledge"` prompt tells the model to answer *only* from `context`. If the LLM is unavailable (quota/429), there's an **extractive fallback**: if the top candidate is reasonably relevant (score ≥ 0.6), it serves that passage verbatim with a `[1]` citation and a note — the user still gets useful information even with no working LLM. Otherwise `text = None`.

If the grounded answer is real (not an abstain), it returns immediately with `general_answer: False` and `claims=[text]`:

```python
if not _is_abstain(text):
    return {"draft_answer": text, "claims": [text], "abstained": False,
            "general_answer": False, "node_path": ["solution_synthesizer"]}
```

If it abstained, control "falls through" to stage 2.

**Stage 2 — general best-effort answer:**

```python
try:
    gtext = (await deps.llm_large.generate(deps.prompts.render("general", input=query))).text.strip()
except Exception as exc:
    return {"abstained": True, "error": str(exc), ...}   # only NOW do we give up
if _is_abstain(gtext):
    return {"abstained": True, ...}
return {"draft_answer": gtext, "claims": [gtext], "abstained": False,
        "general_answer": True, ...}
```

The `"general"` prompt lets the model answer from its own knowledge (no sources). This branch is reached both when retrieval was thin (skipping stage 1 entirely) and when the grounded attempt abstained. The `general_answer: True` flag is important — it tells the confidence gate "this answer legitimately has no KB citations, so don't punish it for that." Only a real exception or an abstaining general answer sets `abstained: True`, which routes to escalation.

**Connects to:** `routing.route_after_synthesizer` reads `abstained`: `True` -> `ticket_creator`, otherwise -> `grounding_verifier`. The `general_answer` flag is read by `routing.decide_confidence` (which delivers general answers directly, bypassing the grounding guard). Uses `deps.llm_large` and three prompt templates (`knowledge`, `general`). Consumes `retrieval.context`, `retrieval.candidates`, `retrieval.sufficient` produced upstream.

---

### `grounding_verifier.py` — reliability gate #2 (does the answer match the sources?)

**Purpose:** The second reliability check ("faithfulness/entailment"). It asks: *is the drafted answer actually supported by the retrieved sources, or did the model hallucinate?* It's built with two efficiency features baked in: **skip-on-strong** (don't waste an LLM call when retrieval is obviously excellent) and **graceful degradation** (a broken verifier must not be mistaken for a hallucination).

**How it works:**

It gathers the drafted answer and the source texts (`sources = [c.text for c in retrieval.candidates]`).

**Skip-on-strong:**

```python
if retrieval.candidates and answer.strip() and retrieval.max_relevance_score >= 0.85:
    score = float(retrieval.max_relevance_score)
    return {"grounding_score": score, "contradiction_flag": False,
            "audit_trail": [{"node": "grounding_verifier", "skipped_llm": True, "score": score}], ...}
```

When retrieval was very strong (top score ≥ 0.85), grounding is *proxied* by that relevance score and the expensive entailment LLM call is skipped entirely. The comment (referencing improvement #10) notes this conserves provider quota "without lowering answer quality" — a near-perfect retrieval match is already strong evidence the answer is grounded. The audit entry marks `skipped_llm: True` so this shortcut is visible.

**The normal path** calls the dedicated verifier and translates its verdict:

```python
verdict = await deps.verifier.verify(answer, sources)
return {"grounding_score": verdict.score,
        "contradiction_flag": not verdict.entailed, ...}
```

`entailed=True` means "the answer follows from the sources", so `contradiction_flag` is its negation.

**Graceful degradation** is the `except` branch:

```python
except Exception as exc:
    fallback = float(state["retrieval"].max_relevance_score or 0.0)
    return {"grounding_score": fallback, "contradiction_flag": False, "error": str(exc),
            "audit_trail": [{"node": "grounding_verifier", "verifier_error": True, "fallback_score": fallback}]}
```

The comment spells out the reasoning: a *transient* judge failure (a 429 or timeout) "must not masquerade as a hallucination and force an escalation." So it falls back to retrieval strength as the grounding proxy and sets `contradiction_flag=False`. A well-retrieved answer still gets through; a weakly-retrieved one still won't clear the confidence gate downstream. This is a deliberate fail-open on infra errors, fail-closed on evidence.

**Connects to:** Always followed by `confidence_gate` (plain edge), which reads `grounding_score` and `contradiction_flag`. Uses `deps.verifier`. Consumes the `draft_answer` and `retrieval.candidates` from the synthesizer/retriever.

---

### `confidence_gate.py` — the central decision maker

**Purpose:** The brain's single most important routing node. It fuses every signal collected so far (intent confidence, retrieval strength, grounding score, contradiction flag, citation quality, whether there's a real answer) into one `Decision`: DELIVER, CLARIFY, RETRY_RETRIEVAL, or ESCALATE. All the earlier nodes gathered evidence; this is where the verdict is rendered.

**How it works:**

It fetches category/sensitivity thresholds (same as the retrieval gate) and pulls the citations and answer. It then calls the pure scoring engine in `confidence.py`:

```python
report = confidence.evaluate(
    intent_confidence=conversation.intent_confidence,
    max_relevance_score=state["retrieval"].max_relevance_score,
    grounding_score=state.get("grounding_score"),
    contradiction=state.get("contradiction_flag", False),
    answer_text=answer,
    num_citations=len(citations),
)
```

`evaluate` (deterministic, no I/O) computes a weighted `final_confidence` — 20% intent, 30% retrieval, 35% grounding, 15% citation quality — and forces confidence to 0 if there's a contradiction. It derives two booleans locally: `citation_valid` (there are citations *and* the citation quality is above zero) and `answer_relevant` (the answer isn't blank).

Then it hands everything to the deterministic policy router `decide_confidence` in `routing.py`. That function's ordering is the actual policy (and the comment stresses "Order matters — hallucination guard first"):

- **(0)** if `general_answer` is set, deliver it if there's a real answer, else escalate — general answers legitimately have no KB citations, so they skip the citation guard.
- **(a)** hard hallucination guard: any contradiction, invalid citation, or empty answer -> ESCALATE (this overrides high self-confidence).
- **(b)** confident *and* grounded (both thresholds met) -> DELIVER.
- **(c)** borderline but there are missing slots and clarification budget remains -> CLARIFY.
- **(d)** thin, but retry budget remains -> RETRY_RETRIEVAL.
- **(e)** otherwise -> ESCALATE.

Back in the node, it writes `final_confidence`, `grounding_score`, and the chosen `decision` into state, plus a rich audit entry including `hallucination_risk`. Two conditional bookkeeping updates finish it:

```python
if decision == Decision.RETRY_RETRIEVAL:
    updates["retry_count"] = state["retry_count"] + 1
if decision == Decision.ESCALATE and not state.get("escalation_reason"):
    updates["escalation_reason"] = ("contradiction" if report.contradiction else "low_confidence")
```

Incrementing `retry_count` is what makes the retry loop *bounded* — once it hits the budget, rule (d) stops firing and (e) escalates. And it records *why* we're escalating for the ticket.

**Connects to:** `routing.route_after_confidence` maps the `decision` to the next node: DELIVER->`responder`, CLARIFY->`info_collector`, RETRY_RETRIEVAL->`query_planner` (the loop), anything else->`ticket_creator`. It's the consumer of everything `grounding_verifier`, `rag_retriever`, and `intent_classifier` produced. Depends on `app.agents.confidence`, `app.agents.routing`, and `deps.thresholds`.

---

### `ticket_creator.py` — build an engineer-ready ticket

**Purpose:** The "Ticket Creator". When the AI can't (or shouldn't) resolve the issue, this node assembles all the collected context into a support ticket and persists it, so a human engineer picks up a fully-populated case rather than a bare "help me".

**How it works:**

`_uuid` is the same safe-parse helper. `_priority(state)` maps sensitivity to priority — `HIGH` sensitivity -> `TicketPriority.HIGH`, else `MEDIUM`. The escalation reason defaults sensibly: `reason = state.get("escalation_reason") or "ai_unresolved"` (the confidence gate usually set this to `"contradiction"` or `"low_confidence"`, but if we arrived some other way, `ai_unresolved` covers it).

It guards missing dependencies/IDs — if `deps.tickets` is `None` or any ID is unparseable, it still returns `decision = ESCALATE` so a human is flagged even though no row was written.

The persistence call assembles the ticket:

```python
ticket = await deps.tickets.create_from_conversation(
    org_id=org_id, conversation_id=conv_id, created_by_user_id=user_id,
    category=category,
    subject=f"[{category}] {state['normalized_query'][:100]}",
    escalation_reason=reason,
    redacted_transcript={"messages": state.get("messages", [])},
    intake_fields=conversation.filled_slots,
    priority=_priority(state),
    final_confidence=state.get("final_confidence"),
)
```

Notice what goes in: a readable subject line (category tag + first 100 chars of the query), the *redacted* transcript (privacy-preserving), the `filled_slots` collected by the classifier/clarifier as structured `intake_fields`, the priority, and the final confidence for triage. On success it returns the `ticket_id`; on failure it still returns `decision = ESCALATE` with the error, so a broken ticket service never silently drops the escalation.

**Connects to:** Always followed by `human_handoff` (plain edge). It is reached from many places: `route_after_ingress` ("human_request"), `route_after_intent` ("out_of_scope"), the retrieval gate, the synthesizer (abstain), the confidence gate (escalate), and the info-collector (budget exhausted). Its `ticket_id` is read by `human_handoff` and `responder`. Uses `deps.tickets`.

---

### `human_handoff.py` — route to the engineer queue + gated notify

**Purpose:** The "Escalation" node. After a ticket exists, this marks the conversation as awaiting a human, records which engineer queue it belongs to, and (best-effort) pings that queue.

**How it works:**

It looks up the correct queue for the category (`deps.categories.get(conversation.category).handoff_queue`) — different problem types go to different teams. Then the gated notification:

```python
if deps.notifications is not None and org_id is not None and ticket_id is not None:
    try:
        await deps.notifications.notify_engineer(org_id=org_id, ticket_id=ticket_id, queue=queue)
    except Exception:
        pass
```

It only notifies when it has a real notifications service *and* valid IDs, and swallows failures — a notification hiccup must not break the run. It then flips the `approval` sub-state:

```python
approval = state["approval"].model_copy(update={"awaiting_human": True, "handoff_queue": queue})
```

`awaiting_human=True` is the flag the responder uses to phrase the "a human will follow up" message and that the rest of the system uses to pause AI autonomy on this thread.

**Connects to:** Always followed by `responder` (plain edge). Reads the `ticket_id` written by `ticket_creator`. The `awaiting_human` flag it sets is read by `responder._compose` and `responder._event_type`. Uses `deps.categories` and `deps.notifications`.

---

### `responder.py` — the single egress: compose, record, reply

**Purpose:** The one and only exit point. Every path through the graph funnels here to produce exactly one user-facing message. It also fires the analytics event and the audit-log entry for the turn. Having a single egress means reply-shaping and logging live in one place instead of being scattered across every branch.

**How it works:**

`_compose(state)` is a priority ladder that turns the internal state into the right sentence:

```python
if state.get("cache_hit") and state.get("cached_answer"):
    return str(state["cached_answer"])          # instant cached reply
control = state["conversation"].control_intent
if control == "greeting": return "Hello! I'm the IT helpdesk assistant. ..."
if control == "cancel":   return "No problem — I've cancelled that. ..."
if state["approval"].awaiting_human or state.get("ticket_id"):
    ...  "I've created a support ticket (ticket X) and routed it to a human engineer..."
if state.get("decision") == Decision.CLARIFY and state.get("draft_answer"):
    return str(state["draft_answer"])           # the clarifying question
if state.get("draft_answer"):
    return str(state["draft_answer"])           # the actual answer
return "I'm sorry, I couldn't find a reliable answer to that."
```

The order matters: cache hits and control phrases are handled first (cheap, no answer was ever computed), then escalation messaging (which includes the ticket number when present), then the clarification question, then a normal answer, and finally a safe fallback.

`_event_type(state)` maps the decision to an analytics label: `DELIVER`->`"auto_resolved"`, `CLARIFY`->`"clarification_requested"`, escalate/ticket->`"escalated"`, else `"chat_answered"`.

The node body composes the text, then records analytics and audit — each independently guarded by a `None` check on the service and a `try/except pass`, because observability must never break the user's reply. Audit uses `actor_type=ActorType.AGENT` to mark this as an AI-generated action. Finally it returns the reply:

```python
return {
    "response_text": text,
    "messages": [{"role": "assistant", "content": text}],
    "node_path": ["responder"],
}
```

Setting `response_text` is the trigger that both flips `memory_manager` into persist mode and makes `route_after_memory` head to `END`. Appending to `messages` records the assistant turn in the transcript.

**Connects to:** Always followed by `memory_manager` (persist mode) then `END`. It's the convergence point for `ingress_guard` (block/cache/greeting/cancel), `intent_classifier` (smalltalk), the confidence gate (DELIVER), `info_collector` (ask user), and the `human_handoff` chain. It reads fields set by nearly every other node — `cached_answer`, `control_intent`, `awaiting_human`, `ticket_id`, `decision`, `draft_answer`. Uses `deps.analytics` and `deps.audit`.

---

### How it all fits together (recap)

The design is a clean assembly line with guard rails:

- **Deterministic bookends** — `ingress_guard` (cheap safety/cache) at the front, `responder` + `memory_manager(persist)` at the back — keep the expensive LLM work sandwiched between fast, predictable steps.
- **Two-part Retriever** (`query_planner` -> `rag_retriever`) separates "decide what to search for" from "search and merge uploaded files."
- **Two reliability gates** (`retrieval_gate` for evidence, `grounding_verifier` for faithfulness) feed **one decision node** (`confidence_gate`), which is the only place a final DELIVER/CLARIFY/RETRY/ESCALATE verdict is made.
- **Answer-first with graceful fallbacks everywhere**: the synthesizer tries grounded, then general, then extractive; the verifier skips-on-strong and degrades on infra errors; the clarifier offers quick replies once; and budgets on retries/clarifications guarantee the graph always terminates in either a delivered answer or a human handoff.
- **Nodes write facts, `routing.py` picks paths, `config_schema.py` injects services** — so the same node code runs identically in production and in tests with fake dependencies.

Key non-node files that make the walkthrough complete: `backend/app/agents/graph.py` (wires the 13 nodes and edges), `backend/app/agents/routing.py` (all the deterministic decisions and edge selectors), `backend/app/agents/confidence.py` (the pure scoring math), and `backend/app/agents/config_schema.py` (the `GraphDeps` dependency bundle every node reaches through `get_deps`).

---

I have read all the service files. Here is the walkthrough section.

## Backend — Services

The `backend/app/services/` package is the **business-logic layer** of the helpdesk. It sits between the thin HTTP route handlers (`app/api/...`) and the low-level data-access `repositories`. A route validates the request and passes it to a service; the service orchestrates one or more repositories, providers (the LLM), and registries to actually *do* the work, and returns models or plain dicts back up.

A few conventions repeat across every file, so it helps to understand them once:

- **Constructor injection.** Each service takes its collaborators (repositories, the DB session, the LLM provider, registries) as constructor arguments. Nothing reaches out to global singletons for its dependencies — they are handed in. This is what makes the services easy to test and lets a single request wire everything together in `app/api/deps.py`.
- **Transaction boundaries live with the request-scoped session.** Most services only *flush* changes through repositories; the request's `get_session` dependency issues the `COMMIT` when the request succeeds. (Two services — `AiDataApi` and `DocSearchService` — are exceptions and commit themselves, which we'll call out.)
- **Async everywhere.** Every method that touches the database is `async` and uses `await`, because the app is built on SQLAlchemy's `AsyncSession`.

The package's `__init__.py` simply re-exports the eight "plain" services so they can be imported as `from app.services import AuthService, ...`:

```python
from app.services.analytics_service import AnalyticsService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
...
```

Notice that `MemoryService`, `KbService`, etc. are re-exported, but the two heavier "AI over the database" services (`AiDataApi`, `DocSearchService`) are **not** in `__init__.py` — they are imported directly from their modules where needed.

---

### `auth_service.py` — `AuthService`

**Purpose**: Owns all authentication business logic: user registration, credential verification, JWT issuance, refresh-token rotation with reuse detection, and logout. It is the security-sensitive heart of the app, so it also writes audit entries for every important event.

**How it works**

The constructor takes four collaborators — the raw `AsyncSession` (for direct lookups), plus three repositories:

```python
def __init__(self, session, users, sessions, audit) -> None:
    self.session = session      # AsyncSession, for org/role lookups
    self.users = users          # UserRepository
    self.sessions = sessions    # UserSessionRepository (refresh-token rows)
    self.audit = audit          # AuditRepository
```

Two small private helpers do read-only lookups against seed/lookup tables. `_get_org_by_slug` finds the tenant organization by its URL-safe slug and insists it be active:

```python
stmt = select(Organization).where(
    Organization.slug == slug, Organization.is_active.is_(True)
)
...
if org is None:
    raise NotFoundError("Organization not found or inactive.")
```

`_get_role_by_key` does the same for a role (e.g. `"end_user"`), raising if the role was never seeded. These raise the app's typed `NotFoundError` rather than returning `None`, so callers don't have to null-check.

**Registration** (`register`) is a clear pipeline:
1. Resolve the org from the slug.
2. Guard against duplicates: `if await self.users.email_exists(org.id, data.email): raise ConflictError(...)`. Note the uniqueness is *scoped to the org* — the same email can exist in two different tenants.
3. Look up the default `END_USER` role.
4. Create the user, hashing the password first: `hashed_password=hash_password(data.password)`. The plaintext password never touches the database.
5. Write an audit entry with `action="auth.register"`.
6. Re-load the user *with its role relationship* (`get_with_role`) because downstream code (building the `Principal`, the API response) needs `user.role.key`. The `assert loaded is not None` documents that the row must exist — it was just created in this same transaction.

**Authentication** (`authenticate`) is where the timing-attack defense lives. This is the subtle part:

```python
user = await self.users.get_active_by_email(org.id, email)
# Verify even when the user is missing to reduce timing side channels.
stored_hash = user.hashed_password if user is not None else _DUMMY_HASH
if not verify_password(password, stored_hash) or user is None:
    raise AuthenticationError("Invalid email or password.")
```

If the email doesn't exist, the code still runs a full (expensive) Argon2 verification against a precomputed `_DUMMY_HASH` defined at the bottom of the file. That way "no such user" and "wrong password" take roughly the same amount of time, so an attacker can't tell which emails are registered by measuring response latency. The error message is deliberately identical for both cases too.

After a good login it does two more things:
- **Transparent rehash**: `if password_needs_rehash(...)` re-hashes the password with current parameters. This lets the app upgrade its hashing cost over time — an old account gets a stronger hash the next time it logs in, invisibly.
- Marks the login time (`mark_logged_in`) and records an `auth.login` audit entry.

**Token issuance** (`issue_token_pair`) mints a short-lived access token and a long-lived refresh token, both stamped with the user's id, org id, and role key so downstream requests can authorize without a DB hit. Crucially, it stores only a **hash** of the refresh token:

```python
await self.sessions.create(
    user_id=user.id,
    refresh_token_hash=hash_refresh_token(refresh.token),
    ...
    expires_at=refresh.expires_at,
)
```

Storing the hash (not the raw token) means a database leak doesn't hand an attacker usable refresh tokens.

**Refresh with reuse detection** (`refresh_tokens`) is the most security-critical method:
1. It decodes and signature-verifies the presented refresh token, mapping any `TokenError` to an `AuthenticationError`.
2. It hashes the token and looks up the matching *active* session row.
3. **Reuse detection**: if the signature is valid but there is no active session for that hash, the token must have already been rotated away or revoked — a hallmark of a stolen, replayed token. The defensive response is to revoke *all* of that user's sessions:
   ```python
   if session_row is None:
       try:
           await self.sessions.revoke_all_for_user(uuid.UUID(decoded.subject))
       except (ValueError, TypeError):
           pass
       raise AuthenticationError("Refresh token is no longer valid.")
   ```
4. Expiry is double-checked against the DB row (not just the JWT claim), revoking on expiry.
5. **Rotation**: on success it revokes the presented session and issues a brand-new pair. Because each refresh burns the old token, a legitimate client always moves forward while a replayed old token trips the reuse check above.
6. Before re-issuing it re-checks the account is still active and not soft-deleted.

Notice `refresh_tokens` imports `TokenType`, `TokenError`, and `decode_token` *inside* the function rather than at the top of the file. That's a deliberate local import to avoid a circular import between the service and `app.core.security`.

**Logout** (`logout`) hashes the token, revokes the matching session if present, and — only if an `org_id` was supplied — writes an `auth.logout` audit entry. It never errors if the session is already gone (idempotent logout).

**Connects to**
- `app.core.security` for all crypto: `hash_password`, `verify_password`, `password_needs_rehash`, `create_access_token`, `create_refresh_token`, `hash_refresh_token`, `decode_token`.
- `UserRepository` / `UserSessionRepository` (`app.repositories.user_repo`) for all user and session persistence.
- `AuditRepository` — writes security events directly (this service does *not* go through `AuditService`).
- `app.core.exceptions` typed errors (`AuthenticationError`, `ConflictError`, `NotFoundError`) which the API layer maps to HTTP status codes.
- Models `Organization`, `Role`, `User`; schema `RegisterRequest`.
- Consumed by the auth API routes and by `deps.py`, which builds the `Principal` from the loaded user.

---

### `audit_service.py` — `AuditService`

**Purpose**: A thin, durable, append-only writer for the audit log (ARCHITECTURE §10). It exists so the *service layer*, where the semantic meaning of an action is known (e.g. `kb.publish`, `ticket.resolve`), can record who did what to which resource, with before/after state.

**How it works**

It wraps a single `AuditRepository`. Its main method `record(...)` takes keyword-only arguments describing the event — `org_id`, `action`, `resource_type`, an `actor_type` that defaults to `ActorType.SYSTEM`, optional actor/resource ids, optional `before`/`after` dicts, and an optional `ip_address`. Its one piece of real logic is automatically attaching the current request's trace id:

```python
return await self.audit.record(
    ...
    trace_id=get_trace_id(),
    before=before,
    after=after,
    ip_address=ip_address,
)
```

`get_trace_id()` reads the trace id from a context variable set by request middleware, so every audit row can be correlated end-to-end with the app logs and `agent_runs` for the same request — without the caller having to pass it around.

The convenience wrapper `record_for_principal(principal, ...)` fills in the actor fields from the request's authenticated `Principal` (setting `actor_type=ActorType.USER`, `actor_user_id=principal.user_id`, `org_id=principal.org_id`). Most call sites use this so they only supply the action-specific fields.

**Connects to**
- `AuditRepository` (`app.repositories.audit_repo`) — the actual DB write.
- `app.core.logging.get_trace_id` — request correlation.
- `Principal` schema and `ActorType` constant.
- Used by many other services/routes for non-auth events. (`AuthService` is a special case: it holds an `AuditRepository` directly and calls `audit.record` itself rather than going through this service.)

---

### `kb_service.py` — `KbService`

**Purpose**: A very small facade over the RAG retrieval stack. It gives the rest of the app one clean method to semantically search the published knowledge base, and one to fetch a document by id.

**How it works**

The constructor takes a `HybridRetriever` and a `KnowledgeRepository`. The core method just normalizes arguments and delegates:

```python
async def semantic_search(self, *, query, org_id, namespace=None, category=None) -> RetrievalOutcome:
    return await self._retriever.retrieve(
        query=query, org_id=str(org_id), namespace=namespace, category=category
    )
```

The only real work here is coercing `org_id` to a string (`str(org_id)`) so callers can pass either a `uuid.UUID` or an already-stringified id — the retriever expects a string. `get_document` is a straight pass-through to the repository. The heavy lifting (embedding the query, hybrid vector + full-text search, reranking) all lives in `HybridRetriever`; this service exists to keep that dependency behind a stable, thin interface.

**Connects to**
- `app.rag.retriever.HybridRetriever` and its `RetrievalOutcome` result type — the actual search engine.
- `KnowledgeRepository` (`app.repositories.kb_repo`) for direct document fetches.
- Consumed by KB API routes and by the agent's retrieval step.

---

### `ticket_service.py` — `TicketService`

**Purpose**: Turns an escalated AI conversation into an engineer-ready ticket, and lets engineers search their queue. This is the "handoff to a human" boundary (Phase 9).

**How it works**

The constructor takes a `TicketRepository` and an optional `CategoryRegistry`, defaulting to the shared singleton:

```python
self._categories = categories or get_category_registry()
```

`create_from_conversation(...)` is the important method and it is **idempotent per conversation thread**. It first checks whether a ticket already exists for this conversation and, if so, returns it unchanged:

```python
existing = await self._tickets.get_by_conversation(conversation_id)
if existing is not None:
    return existing
```

This matters because an escalation might fire more than once (retries, double-clicks); this guard guarantees at most one ticket per conversation.

Next it does **queue routing** by asking the category registry which engineering queue handles this category:

```python
queue = self._categories.get(category).handoff_queue
```

Then it creates the ticket with a rich payload — status `OPEN`, the resolved `assigned_queue`, the subject, `intake_fields`, the `escalation_reason`, an optional `final_confidence` score from the AI, structured `engineer_hints`, and a `redacted_transcript` (the conversation with PII stripped, so engineers can see context safely). Finally it writes a `CREATED` event to the ticket's event log:

```python
await self._tickets.add_event(
    ticket_id=ticket.id,
    event_type=TicketEventType.CREATED,
    payload={"escalation_reason": escalation_reason, "queue": queue},
)
```

The event log is the ticket's audit trail — every state change appends an event.

`search(...)` is a straightforward wrapper over `list_queue`, letting an engineer pull recent tickets in a given queue, optionally filtered by a set of statuses, with a `limit` (default 20).

**Connects to**
- `TicketRepository` (`app.repositories.ticket_repo`).
- `CategoryRegistry` (`app.registries.category_registry`) for category → queue routing.
- Constants `TicketStatus`, `TicketPriority`, `TicketEventType`.
- Called by the agent's escalation step and by ticket/queue API routes.

---

### `notification_service.py` — `NotificationService`

**Purpose**: Persists outbound notifications (mainly the "a ticket was handed off to you" message). Note the docstring: it is **send-only and gated** — this service just *records* the notification as `PENDING`; a separate worker actually delivers it. Nothing here sends an email or a push directly.

**How it works**

`notify_engineer(...)` chooses a delivery channel based on whether a specific recipient is known:

```python
channel = (
    NotificationChannel.IN_APP if recipient_user_id else NotificationChannel.QUEUE
)
```

If we know exactly which engineer to notify, it's an in-app notification for that user; otherwise it's a queue-level notification (any engineer watching that queue). It builds the payload by merging the queue name with any extra payload, then creates the row as `PENDING`:

```python
body = {"queue": queue, **(payload or {})}
return await self._notifications.create(
    ...
    status=NotificationStatus.PENDING,
    ticket_id=ticket_id,
)
```

The `PENDING` status is the "gate": actual dispatch is a deliberate, separate worker step, which keeps side-effectful sending out of the request path.

**Connects to**
- `NotificationRepository` (`app.repositories.notification_repo`).
- Constants `NotificationChannel`, `NotificationType` (defaults to `HANDOFF`), `NotificationStatus`.
- Typically called alongside `TicketService.create_from_conversation` when a ticket is handed off.

---

### `memory_service.py` — `MemoryService`

**Purpose**: The conversation memory engine (ARCHITECTURE §4). It implements the three-tier memory model that keeps the AI's token cost flat no matter how long a conversation runs:
- **short-term** — a rolling window of the most recent messages,
- **long-term** — a rolling LLM-written summary of everything older,
- **durable** — per-user facts that persist across conversations.

**How it works**

The constructor injects the two repositories and the LLM provider, plus two tuning knobs with defaults:

```python
window_turns: int = 10,          # how many recent messages to keep verbatim
summary_trigger_turns: int = 12, # start summarizing after this many turns
```

`load_state(...)` assembles the current `MemoryState` the agent needs before answering:
1. Pull up to 200 messages, then keep only the last `window_turns` as the short-term window:
   ```python
   window = [{"role": m.role, "content": m.content} for m in messages[-self._window_turns:]]
   ```
2. Fetch the current rolling summary row (long-term).
3. Fetch this user's durable facts as a `{key: value}` dict.
4. Package all three plus `covered_through_turn` (how far the summary already covers) into a `MemoryState`.

`save_fact(...)` is the explicit path used by the agent's `save_memory` tool — it upserts one durable fact for a user via `MemoryRepository.upsert_fact`.

`persist_turn(...)` is the post-response maintenance step and holds the compression logic. Summarization only fires when the conversation is long enough *and* has grown past what the summary already covers:

```python
if turn_id >= self._summary_trigger_turns and turn_id > state.covered_through_turn:
    updated = await self._summarize(...)
```

This is the key idea: instead of feeding the whole transcript to the LLM every turn (cost grows without bound), older turns get folded into a compact summary once, and only recent turns stay verbatim.

`_summarize(...)` renders the `"summarizer"` prompt with the prior summary plus the recent window, calls `self._llm.generate(...)`, stores the new summary with `add_summary(covered_through_turn=turn_id)`, and returns a fresh `MemoryState`.

`_extract_and_store_facts(...)` is the durable-memory writer. It renders the `"memory_updater"` prompt and asks the LLM for **structured** output validated against a Pydantic model:

```python
class _FactsResult(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict)
...
extracted: _FactsResult = await self._llm.generate_structured(
    self._prompts.render("memory_updater", transcript=transcript), _FactsResult,
)
```

It is deliberately **best-effort** — the whole call is wrapped in `try/except Exception`, and on failure it just logs a warning and returns, because losing a fact extraction must never break the user's reply. Each non-empty `key`/`value` is upserted and mirrored into the in-memory `into.facts` dict so the running state stays consistent.

**Connects to**
- `MemoryRepository` and `ConversationRepository` for persistence.
- `LLMProvider` (`app.providers.base`) for both `generate` (summaries) and `generate_structured` (fact extraction).
- `PromptRegistry` (`app.registries.prompt_registry`) for the `"summarizer"` and `"memory_updater"` prompt templates.
- `MemoryState` (`app.agents.state`) — the shared shape the agent graph consumes.
- Driven by the agent orchestration: `load_state` before a turn, `persist_turn` after.

---

### `analytics_service.py` — `AnalyticsService`

**Purpose**: A minimal append-only writer for the product analytics event stream (deflections, escalations, ratings, etc.), used for dashboards and reporting.

**How it works**

One method, `record(...)`, takes an `event_type` string plus a set of optional foreign keys (`user_id`, `conversation_id`, `ticket_id`), a `category`, and a free-form `properties` dict. It normalizes a missing `properties` to `{}` and forwards everything to the repository:

```python
return await self._analytics.record_event(
    org_id=org_id, event_type=event_type, ...,
    properties=properties or {},
)
```

That's the whole service — the value is a single, consistent choke point for emitting analytics events so event shapes stay uniform.

**Connects to**
- `AnalyticsRepository` (`app.repositories.analytics_repo`) and the `AnalyticsEvent` model.
- Called throughout the app wherever a business event worth measuring occurs.

---

### `feedback_service.py` — `FeedbackService`

**Purpose**: Records end-user feedback on AI answers (thumbs up/down plus optional comment) and lets a background job pull the not-yet-processed items (e.g. to feed a learning/quality loop).

**How it works**

`submit(...)` creates a `Feedback` row tying together the org, user, conversation, and a `FeedbackRating`, with optional `message_id`, `ticket_id`, and `comment`. The one bit of logic is generating a stable handle when the caller doesn't supply one:

```python
feedback_handle=feedback_handle or generate_jti(),
```

`generate_jti()` (borrowed from `app.core.security`) mints a unique, random, URL-safe id. This `feedback_handle` gives each feedback item a public identifier that isn't the raw database primary key — safe to expose in URLs or reference externally.

`list_unprocessed(limit=100)` is a straight pass-through to the repository for the processing job.

**Connects to**
- `FeedbackRepository` (`app.repositories.feedback_repo`) and the `Feedback` model.
- `FeedbackRating` constant; `generate_jti` from `app.core.security`.
- Called by the feedback API route and a downstream processing worker.

---

### `ai_data_api.py` — `AiDataApi`

**Purpose**: A **100%-LLM natural-language interface to the database**. An admin types a plain-English instruction ("how many open tickets are there?", "create a ticket about the VPN outage", "close ticket 1234"); the LLM picks exactly one predefined data operation and its arguments, the service runs it against PostgreSQL (read *or* write), and the LLM turns the raw result back into a plain-English answer. It is the classic **plan → execute → explain** loop.

**How it works**

At module load it defines the **tool catalog** — the closed set of operations the LLM is allowed to choose from. Each entry has a stable `name`, an `args` hint, and a description, and the comment stresses the names must stay stable because the LLM has to echo them back:

```python
TOOLS = [
    {"name": "count_tickets", "args": "status?, category?", "desc": "Count tickets..."},
    {"name": "list_tickets", ...},
    {"name": "create_ticket", "args": "subject, category?, priority?", "desc": "CREATE a new ticket (manipulates data)."},
    {"name": "update_ticket_status", ...},
    {"name": "count_knowledge", ...},
    {"name": "search_knowledge", ...},
]
_VALID_STATUS = {s.value for s in TicketStatus}
_VALID_PRIORITY = {p.value for p in TicketPriority}
```

Building `_VALID_STATUS`/`_VALID_PRIORITY` from the enums means validation always stays in sync with the real domain values.

The plan the LLM must return is a small Pydantic model:

```python
class DataPlan(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
```

The constructor takes a DB `session` and an `llm`, and builds its own repositories internally (`TicketRepository`, `KnowledgeRepository`, `ConversationRepository`).

**`run(...)`** is the public entrypoint and shows the three phases plus its error contract:

```python
plan, planner = await self._plan(instruction)
try:
    result = await self._execute(plan, principal)
    ok = True
except Exception as exc:  # noqa: BLE001
    result = {"error": str(exc)}
    ok = False
answer = await self._explain(instruction, plan, result, ok)
return {"answer": answer, "tool": plan.tool, "args": plan.args, "planner": planner, "result": result}
```

Any execution failure is *caught and turned into data* (`{"error": ...}`) rather than raised, so the LLM can still narrate a graceful "couldn't do that" answer. The returned dict exposes the chosen tool, its args, which planner was used, and the raw result — useful for transparency and debugging in the UI.

**Phase 1 — `_plan` (LLM chooses the operation).** It renders the tool catalog into the system prompt, tells the LLM the exact legal status/priority values, and demands JSON only, then asks for structured output:

```python
plan = await self.llm.generate_structured(messages, DataPlan)
if plan.tool in {t["name"] for t in TOOLS}:
    return plan, "llm"
```

It validates the returned tool name against the catalog — the LLM can't invent an operation. If the LLM call fails (e.g. quota) *or* returns an unknown tool, it falls through to `_keyword_plan`, a tiny deterministic keyword matcher, and reports `planner="keyword-fallback"`. This fallback exists purely so a demo still works when the LLM provider is down; the LLM is the intended path.

**Phase 2 — `_execute` (run against the database).** This is a dispatch table on `plan.tool`. Note the defense at the top:

```python
org = principal.org_id
a = plan.args or {}
status = a.get("status") if a.get("status") in _VALID_STATUS else None
category = a.get("category")
```

Every query is scoped to `principal.org_id` (tenant isolation — the LLM cannot reach another org's data), and any `status` the LLM produced is validated against the enum before use, so a hallucinated status silently becomes `None` rather than crashing. Read operations map to repository calls (`count_for_org`, `list_for_org`, `get_for_org`, `count_documents`, `search_documents`) or the private `_group` aggregator; `list`/`search` limits are clamped with `min(int(...), 20)` so the LLM can't ask for an unbounded result set.

`_group` is a small grouped-count helper using raw SQLAlchemy, excluding soft-deleted rows:

```python
stmt = (select(column, func.count())
        .where(Ticket.org_id == org, Ticket.deleted_at.is_(None))
        .group_by(column))
```

The two **write** operations are where this service "manipulates data":
- `_create_ticket` sanitizes inputs against the registry and enums (an invalid category defaults to `"application_error"`, invalid priority to `"medium"`, subject truncated to 200 chars), creates a backing conversation in `AWAITING_HUMAN` status, builds a `Ticket`, then `flush()` + **`commit()`**.
- `_update_status` re-validates the target status, loads the org-scoped ticket, records the old value, updates it, appends a `STATUS_CHANGED` ticket event with `from_status`/`to_status`, then `commit()`.

Both call `commit()` themselves — unlike the "plain" services, this one owns its transaction because it's invoked outside the normal commit-on-success request flow. Ids from the LLM are parsed defensively via the module helper `_as_uuid`, which returns `None` on anything unparseable instead of throwing.

**Phase 3 — `_explain` (LLM narrates the result).** It sends the original question plus the raw `result` dict and asks for a 1–3 sentence answer grounded *only* in the data ("Do not invent"). If the LLM call fails or returns empty, it falls back to `_template_answer`, a deterministic formatter that inspects the result shape (`"count"`, `"by_status"`, `created`, `updated`, `tickets`, `articles`, …) and produces a plain sentence. So the pipeline degrades gracefully at both the planning and explaining ends.

**Connects to**
- `LLMProvider` via `ChatMessage` and `generate_structured` / `generate`.
- `TicketRepository`, `KnowledgeRepository`, `ConversationRepository`; models `Ticket`, `Conversation`.
- `CategoryRegistry` (`get_category_registry`) for category → queue routing on create.
- Constants `TicketStatus`, `TicketPriority`, `TicketEventType`, `ConversationStatus`.
- Exposed via an admin "ask the data" API route; not re-exported from `services/__init__.py`.

---

### `docsearch_service.py` — `DocSearchService`, `UploadsSearcher`, and parser helpers

**Purpose**: The Document-Intelligence service. It lets a user upload files (PDF, Word, plain text, one tab of an Excel workbook) or a public URL, splits each source into location-tagged passages, indexes them for Postgres full-text search, and answers searches with LLM-written one-line "thumbnails" while preserving the verbatim original text for drill-down. `UploadsSearcher` is a companion that lets the AI chat answer from any file uploaded across the org.

**How it works — module-level parsing helpers**

`_split(text, size=1000)` is the chunker. It tries to break on blank lines (paragraph boundaries) so chunks stay readable, flushing the current buffer before it would exceed `size`, and hard-splitting only when a single paragraph is more than twice the target size:

```python
for para in re.split(r"\n\s*\n", text):
    if cur and len(cur) + len(para) > size:
        out.append(cur.strip()); cur = ""
    cur += para + "\n\n"
    while len(cur) > size * 2:
        out.append(cur[:size].strip()); cur = cur[size:]
```

Each parser returns a list of `(location, text)` tuples, where **location** is a human-readable citation the UI can show:
- `_parse_pdf` uses `pypdf`, iterating pages (1-indexed) and tagging chunks `"Page 3"` or `"Page 3 · part 2"` when a page splits.
- `_parse_docx` uses `python-docx`, joins non-empty paragraphs, and tags chunks `"Section N"`.
- `_parse_excel` uses `openpyxl` in read-only/data-only mode. It picks the requested sheet (falling back to the first), treats row 0 as the header, and renders each subsequent non-empty row as `header: value` pairs, one field per line, tagged `"Sheet <name> · Row <n>"`. This turns a spreadsheet row into readable prose. It returns the resolved sheet name too. `sheet_names(data)` is a small companion so the UI can ask the user which tab to index.
- `_parse_text` decodes UTF-8 (ignoring bad bytes) and tags chunks `"Part N"`.

**URL fetching with SSRF protection.** `fetch_url` is guarded by `_url_is_safe`, which is a real security control — it blocks anything that isn't public http/https:

```python
if p.scheme not in ("http", "https") or not p.hostname: return False
if host in ("localhost", "127.0.0.1", "::1"): return False
ip = ipaddress.ip_address(socket.gethostbyname(host))
if ip.is_private or ip.is_loopback or ip.is_link_local: return False
```

This resolves the hostname to an IP and rejects private/loopback/link-local ranges, so a user can't trick the server into fetching internal-network resources (an SSRF attack). Only if the URL passes does it `httpx.get` (with a 15s timeout), strip `<script>/<style>/<noscript>` blocks and all remaining tags with regex, collapse whitespace, and chunk into `"Section N"` passages.

**`DocSearchService`** takes a `session` and an `llm`.

- `ingest(...)` caps passages at `_MAX_CHUNKS` (4000), creates one `UploadedDocument` row scoped to the uploader's org and user, then adds an `UploadedChunk` per passage carrying `chunk_index`, `location`, and verbatim `text`. It **commits itself** and returns a small summary dict. Both the document and its chunks are stamped with `org_id` *and* `user_id` — this data is private to the uploader.
- `list_documents` / `delete_document` are the per-user library. Delete is careful: it loads the doc and returns `False` unless it belongs to this org *and* this user, guarding against deleting someone else's document:
  ```python
  if doc is None or doc.org_id != principal.org_id or doc.user_id != principal.user_id:
      return False
  ```

- `search(...)` is the interesting one. It builds an OR-style tsquery from the user's words via `_or_tsquery_terms` (reused from the KB repo so behavior matches the main KB search), and if there are no usable terms returns empty hits immediately. It ranks with `ts_rank` and filters to the uploader's own chunks:
  ```python
  tsquery = func.to_tsquery("english", terms)
  rank = func.ts_rank(UploadedChunk.text_fts, tsquery).label("rank")
  stmt = (select(UploadedChunk, ...filename, ...source_type, rank)
          .join(UploadedDocument, ...)
          .where(UploadedChunk.org_id == ..., UploadedChunk.user_id == ...,
                 UploadedChunk.text_fts.op("@@")(tsquery))
          .order_by(rank.desc()).limit(limit))
  ```
  It shapes each row into a hit carrying the verbatim `text` (for drill-down), a rounded `score`, and an empty `summary` placeholder. Then it asks the LLM to summarize the top 5 hits **in a single call** (the docstring calls this "quota-safe" — one LLM round-trip per search, not one per hit). Any hit still lacking a summary (LLM failed, or ranked below the top 5) falls back to a cleaned 200-char snippet of the raw text. So there is always something readable.

- `_summarize(...)` numbers the passages, asks for strict JSON with one sentence per passage **in order** validated against `_Summaries`, and on any exception returns `[]` — again, search must never break because the LLM hiccuped.

**`UploadsSearcher`** is a lighter, **org-wide** searcher used by the AI chat pipeline (not the personal document UI). It takes just a `session`. Its `search(org_id, query, limit=4)` runs essentially the same full-text query but scoped to the whole **org** (no `user_id` filter — so the assistant can answer from any file anyone in the org uploaded) and returns `RetrievedChunk` objects so the results slot straight into the agent's retrieval format:

```python
out.append(RetrievedChunk(
    chunk_id=str(c.id), doc_id=str(c.doc_id), text=c.text,
    score=0.75, rerank_score=0.75,  # an FTS hit in an uploaded file is a real match
    source_uri=f"upload://{fname} · {c.location}", category_key=None,
))
```

The fixed `0.75` scores treat any full-text match in an uploaded file as a solid hit. The whole query is wrapped in `try/except` returning `[]` — the comment says it plainly: "never break chat if uploads search fails." The `source_uri` uses an `upload://` scheme with the readable location so citations in chat point back at the exact file and spot.

**Connects to**
- Models `UploadedDocument`, `UploadedChunk` (`app.models.docsearch`), including the `text_fts` full-text column.
- `_or_tsquery_terms` reused from `app.repositories.kb_repo` (shared query-building with the KB).
- `LLMProvider` via `ChatMessage` / `generate_structured` for thumbnails.
- `RetrievedChunk` (`app.agents.state`) — so `UploadsSearcher` feeds the same agent retrieval path as the KB retriever.
- Third-party parsers `pypdf`, `python-docx`, `openpyxl`, and `httpx`, all imported lazily inside their functions so the dependency is only loaded when that file type is actually used.
- `DocSearchService` backs the personal upload/search API routes; `UploadsSearcher` is wired into the chat agent's retrieval step. Neither is re-exported from `services/__init__.py`.

---

## Backend — API layer

This is the HTTP boundary of the whole system. Everything a browser or external client can reach lives here: the routers that define each URL, the dependency-injection (DI) wiring that hands each route its database session and its verified caller, and the error handlers that turn internal exceptions into clean JSON. Nothing in this layer contains business logic itself — instead it *composes* the pieces (repositories, services, the AI engine) and enforces who is allowed to call what.

A mental model to keep while reading:

- **Routers** (`v1/*.py`) define endpoints. They are thin: parse the request, call a service/repository, shape the response.
- **`deps.py`** is the "assembly line." FastAPI calls these functions before your route runs and injects the results as arguments.
- **`errors.py`** catches anything that goes wrong and formats it consistently.
- **`health.py`** is the ops probe used by Kubernetes/load balancers.
- **`router.py`** glues all the v1 routers together under one prefix.

---

### `backend/app/api/__init__.py`

**Purpose**: Marks the `api` folder as a Python package and documents what lives inside it.

**How it works**: It is a single docstring — `"""API layer (HTTP boundary): routers, DI, and error handlers."""`. No code. Its only job is to make `app.api` importable and to tell a reader what the package is for.

**Connects to**: Nothing directly; it just enables `from app.api.deps import ...` and `from app.api.v1 import ...` elsewhere.

---

### `backend/app/api/deps.py`

**Purpose**: The central dependency-injection module. Every reusable "thing a route needs" — a DB session, the authenticated user, RBAC guards, pagination, the AI engine bundle — is defined here once and reused everywhere. The file docstring calls this the place "where the HTTP layer is composed."

The key idea behind this file: FastAPI's `Depends(...)`. When a route parameter is annotated as `Depends(some_function)`, FastAPI runs `some_function` first and passes its return value into the route. Dependencies can themselves depend on other dependencies, forming a chain. This file builds that chain and then wraps each step in a typed alias (using `Annotated[...]`) so routes can just write `session: SessionDep` instead of the verbose full form.

**How it works** — walking the important parts top to bottom:

**1. The bearer scheme.**
```python
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")
```
`HTTPBearer` reads the `Authorization: Bearer <token>` header. The important flag is `auto_error=False`: normally FastAPI would auto-raise its own 401 when the header is missing, but here we turn that off so *we* can raise our own domain error (`AuthenticationError`) that flows through the RFC 7807 formatter in `errors.py`. Consistency of error shape is the goal.

**2. The DB session.**
```python
async def get_session() -> AsyncIterator[AsyncSession]:
    async for session in _get_session():
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
```
This is a thin re-export of the real session factory from `app.db.session`. It is `async` and uses `yield`, which is the FastAPI pattern for "setup, hand it over, tear down" — the session is opened, yielded to the route, and closed automatically when the request finishes. `SessionDep` is the alias routes actually use: writing `session: SessionDep` means "give me a live DB session for this request."

**3. Service factories.** `get_auth_service` shows the pattern used throughout:
```python
def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(
        session=session,
        users=UserRepository(session),
        sessions=UserSessionRepository(session),
        audit=AuditRepository(session),
    )
```
Notice it depends on `SessionDep`, so the same request-scoped session is threaded into the service and all its repositories. This guarantees everything in one request shares one transaction. `AuthServiceDep` is the alias.

**4. `get_current_user` — identity from the token, never the body.** This is the security heart of the file.
```python
async def get_current_user(session, credentials=Depends(_bearer_scheme)) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing or malformed Authorization header.")
    try:
        decoded = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc
```
Step by step: (a) if there's no bearer header, reject; (b) decode and verify the JWT, insisting it is specifically an **access** token (`expected_type=TokenType.ACCESS`) so a refresh token cannot be replayed here; (c) turn the token's `subject` into a UUID, rejecting garbage:
```python
    user_id = uuid.UUID(decoded.subject)
```
(d) load the user with their role eager-loaded, and reject inactive/deleted accounts:
```python
    user = await UserRepository(session).get_with_role(user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("The account is inactive or no longer exists.")
    return user
```
The crucial security property (called out in the docstring): identity is *always* taken from the verified token, never from anything the client puts in the request body. `CurrentUser = Annotated[User, Depends(get_current_user)]`.

**5. `get_current_principal` — the immutable caller snapshot.**
```python
def get_current_principal(user: CurrentUser) -> Principal:
    return Principal.from_user(user)
```
It converts the ORM `User` into a `Principal` — a frozen dataclass (see `schemas/auth.py`) holding `user_id`, `org_id`, `email`, `role`, and the computed `permissions` set. Most routes prefer `CurrentPrincipal` over `CurrentUser` because it is lightweight, immutable, and already carries the RBAC info. `CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]`.

**6. RBAC guard factories.** These are *factories*: you call them to build a dependency.
```python
def require_roles(*roles: str):
    allowed = tuple(roles)
    def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(allowed):
            raise ForbiddenError(...)
        return principal
    return _guard
```
`require_roles("admin")` returns a `_guard` function; a route uses it as `dependencies=[Depends(require_roles("admin"))]`. Because `_guard` itself depends on `CurrentPrincipal`, requiring a role transitively requires authentication too. `require_permissions` is the same shape but calls `principal.has_all_permissions(required)` — the caller must hold **every** listed permission, not just one. Roles are coarse ("are you an engineer?"); permissions are fine-grained ("can you write KB articles?").

**7. `client_ip` — proxy-aware IP.**
```python
def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
```
Prefers the first hop in `X-Forwarded-For` (set by a reverse proxy), falling back to the direct socket peer. Used by auth routes for audit logging of logins.

**8. Shared helpers.** `get_audit_service` (+ `AuditServiceDep`), `RedisDep` (a Redis connection), and pagination:
```python
def pagination_params(page=Query(ge=1)=1, size=Query(ge=1, le=100)=20) -> PaginationParams:
    return PaginationParams(page=page, size=size)
```
This validates `?page=` and `?size=` query strings (page ≥ 1, size between 1 and 100) and hands back a `PaginationParams` object that later exposes `.limit`/`.offset`. `PaginationDep` is used by every list endpoint.

**9. `get_graph_deps` — the AI-engine dependency bundle (the big one).** This assembles *everything* the LangGraph AI engine needs for one request into a single `GraphDeps` object:
```python
def get_graph_deps(session: SessionDep) -> GraphDeps:
    settings = get_settings()
    embedder = get_embedding_provider()
    kb_repo = KnowledgeRepository(session)
    retriever = HybridRetriever(
        DenseRetriever(ChromaVectorStore(), embedder),
        SparseRetriever(kb_repo),
        HeuristicReranker(),
        top_k=settings.RETRIEVAL_TOP_K,
        candidate_k=settings.RETRIEVAL_CANDIDATE_K,
    )
    ...
```
The retriever is built as a **hybrid**: a dense (vector/embedding) retriever backed by ChromaDB, a sparse (keyword) retriever backed by the SQL KB repo, and a heuristic reranker to merge/order candidates. Then it wires a `MemoryService` (conversation summarization/window), and returns a `GraphDeps` packing together: both LLM sizes (`llm_large`, `llm_small`), the embedder, a verifier provider, the retriever, memory, and the domain services (`KbService`, `TicketService`, `NotificationService`, `AnalyticsService`, `FeedbackService`, `AuditService`), plus the registries (`prompts`, `categories`, `thresholds`, `tools`), the `users`/`conversations` repositories, a raw `redis` client, and — as the task highlights — `uploads=UploadsSearcher(session)`. That `uploads` field lets the agent search a user's *attached* documents (the docsearch feature) as part of answering, so the AI can ground answers in files the user attached, not just the org KB. `GraphDepsDep` is the alias.

**10. `get_engine`.**
```python
def get_engine() -> HelpdeskAIEngine:
    return get_ai_engine()
```
The comment is the key insight: "graph compiled once, deps per-request." The expensive part (compiling the LangGraph state machine) happens once at process startup and is returned here as a singleton; the cheap, request-specific part (`GraphDeps`, holding the live session) is built fresh per request via `get_graph_deps`. `AiEngineDep` is the alias.

**11. `__all__`** at the bottom lists every public symbol so `from app.api.deps import *` is well-defined and so tooling knows the intended surface.

**Connects to**: This is the hub. `errors.py` handles the `AuthenticationError`/`ForbiddenError` it raises; every v1 router imports its aliases (`SessionDep`, `CurrentPrincipal`, `PaginationDep`, `require_roles`, `require_permissions`, `AiEngineDep`, etc.). It pulls the session from `app.db.session`, token logic from `app.core.security`, RBAC from `app.core.rbac` (via `Principal`), providers from `app.providers.registry`, RAG classes from `app.rag.*`, registries from `app.registries.*`, repositories from `app.repositories.*`, and services from `app.services.*`. In effect `deps.py` is where the layered architecture is stitched together.

---

### `backend/app/api/errors.py`

**Purpose**: Translate every kind of failure into a consistent **RFC 7807 "problem+json"** response, and stamp each one with the request's `trace_id` so a client error can be matched to server logs and `agent_runs`. Registered onto the app in `app.main`.

**How it works**:

The shared builder produces the standard problem shape:
```python
def _problem(*, status_code, title, detail, errors=None) -> JSONResponse:
    body = {
        "type": f"about:blank#{title}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "trace_id": get_trace_id(),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(status_code=status_code, content=body,
                        media_type="application/problem+json")
```
Every response has `type`, `title`, `status`, `detail`, and `trace_id`; the optional `errors` list carries field-level validation detail. The media type is the RFC-mandated `application/problem+json`, which tells clients "this is a structured error."

There are four handlers, each mapping one exception category:

- **`app_error_handler`** — for `AppError`, the base of all *domain* exceptions (auth, forbidden, not-found, validation, etc.). It trusts the exception to know its own HTTP status and machine-readable code: `status_code=exc.status_code, title=exc.error_code, detail=exc.message`, and passes through `exc.details` as the `errors` list when it is a list. This is how `AuthenticationError` becomes a clean 401 and `ForbiddenError` a 403.
- **`validation_error_handler`** — for FastAPI's `RequestValidationError` (bad request bodies/params). It reshapes Pydantic's errors into `{loc, msg, type}` entries and returns **422** with `title="validation_error"`.
- **`http_exception_handler`** — for Starlette's `HTTPException` (e.g. raw 404s from the framework itself). It reuses the problem format and, importantly, re-attaches any headers the exception carried (`if exc.headers: response.headers.update(...)`) — this matters for things like `WWW-Authenticate` or `Retry-After`.
- **`unhandled_error_handler`** — the safety net for *any* uncaught `Exception`. It logs the full stack trace (`_logger.exception(...)`) but returns a deliberately vague **500** ("An unexpected error occurred.") so internal details never leak to clients.

Finally:
```python
def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
```
This is called from `app.main` at startup to install all four.

**Connects to**: Consumes `AppError` and its subclasses from `app.core.exceptions` (which `deps.py` and every router raise, e.g. `NotFoundError`, `ValidationError`, `AuthenticationError`, `ForbiddenError`). Uses `app.core.logging` for `get_logger` and `get_trace_id` (the trace id itself is set by middleware upstream). It is the reason routers can just `raise NotFoundError("...")` and trust a correct JSON 404 will come out.

---

### `backend/app/api/health.py`

**Purpose**: Liveness and readiness probes for orchestration (Kubernetes, load balancers). Mounted at the root, *outside* `/api/v1`, so infra can hit them without auth or versioning.

**How it works**:

A small shared payload describes the running service:
```python
def _liveness_payload() -> dict:
    settings = get_settings()
    return {"status": "ok", "service": settings.APP_NAME,
            "version": settings.VERSION, "environment": settings.APP_ENV}
```

**Liveness** — `GET /health` and its alias `GET /health/live` — just returns that payload. "Liveness" means "the process is up and serving"; it deliberately does **not** touch any external dependency, so a transient DB blip won't cause the orchestrator to kill an otherwise-healthy pod.

**Provider check**:
```python
def providers_configured() -> bool:
    provider = settings.LLM_PROVIDER.lower()
    if provider == "fake":
        return True
    key = {"gemini": settings.GEMINI_API_KEY, "openai": settings.OPENAI_API_KEY,
           "claude": settings.ANTHROPIC_API_KEY}.get(provider)
    return bool(key and key.get_secret_value())
```
It confirms the *active* LLM provider actually has an API key (the `fake` provider used in tests is always "configured"). `get_secret_value()` unwraps a Pydantic `SecretStr`.

**Readiness** — `GET /health/ready` — is the real dependency check:
```python
database, redis_ok, chroma = await asyncio.gather(check_database(), check_redis(), check_chroma())
celery_ok = await asyncio.to_thread(check_celery)
checks = {"database": ..., "redis": ..., "chroma": ..., "celery": ..., "providers": providers_configured()}
healthy = all(checks.values())
payload = {"status": "ready" if healthy else "not_ready", "checks": checks}
if not healthy:
    return JSONResponse(status_code=503, content=payload)
return payload
```
The three async checks (Postgres, Redis, Chroma) run concurrently via `asyncio.gather`; the Celery broker check is synchronous so it is pushed to a thread with `asyncio.to_thread` to avoid blocking the event loop. If *any* dependency is down, it returns **503** so the load balancer stops routing traffic to this instance until it recovers; otherwise **200**. The file docstring notes the check callables are module-level attributes precisely so tests can monkeypatch them without real backends.

**Connects to**: Pulls the individual probes from their owning modules — `check_database` (`app.db.session`), `check_redis` (`app.core.redis`), `check_chroma` (`app.rag.vectorstore`), `check_celery` (`app.workers.queue`) — and `get_settings` (`app.core.config`). This router is included by `app.main` directly at the root, unlike the v1 routers which go through `router.py`.

---

### `backend/app/api/v1/__init__.py`

**Purpose**: Package marker for the versioned routers. Docstring: `"""API v1 routers (mounted under the configured API_V1_PREFIX)."""`.

**Connects to**: Enables `from app.api.v1 import auth, chat, ...` in `router.py`.

---

### `backend/app/api/v1/router.py`

**Purpose**: Aggregate every v1 sub-router into one `api_router` that `app.main` mounts under the version prefix (e.g. `/api/v1`).

**How it works**:
```python
api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
api_router.include_router(tickets.router)
api_router.include_router(kb.router)
api_router.include_router(analytics.router)
api_router.include_router(notifications.router)
api_router.include_router(feedback.router)
api_router.include_router(ai_data.router)
api_router.include_router(docsearch.router)
```
Each sub-router already declares its own `prefix` (e.g. `/auth`, `/chat`) and `tags`, so this file just composes them. Adding a new feature area is a two-line change here plus the import. `__all__ = ["api_router"]` exposes the single aggregate.

**Connects to**: Imports all ten routers from `app.api.v1.*`; consumed by `app.main`, which does the final `app.include_router(api_router, prefix=API_V1_PREFIX)`. (`health.router` is intentionally *not* here — it lives at the root.)

---

### `backend/app/api/v1/auth.py`

**Purpose**: The authentication surface: register, login, refresh, logout, and "who am I." Router prefix `/auth`.

**How it works**:

A private helper standardizes the token response:
```python
def _token_response(access, refresh) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(access_token=access.token, refresh_token=refresh.token,
                         token_type="bearer",
                         expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
```
It reports `expires_in` in **seconds** (config is in minutes), which is what OAuth-style clients expect.

- **`POST /register`** → `201 UserResponse`. Delegates to `service.register(payload)` and serializes the created ORM user with `UserResponse.model_validate(user)`. The request (`RegisterRequest`) carries `org_slug` so the correct tenant is resolved (multi-tenant: users are unique per org).
- **`POST /login`** → `TokenResponse`. Authenticates against `(org_slug, email, password)`, then issues a token pair, recording device context for the session/audit trail:
  ```python
  access, refresh = await service.issue_token_pair(
      user, user_agent=request.headers.get("user-agent"), ip_address=client_ip(request))
  ```
  Note `client_ip(request)` — the proxy-aware helper from `deps.py`.
- **`POST /refresh`** → `TokenResponse`. Rotates the refresh token (`service.refresh_tokens(payload.refresh_token, ...)`), again capturing user-agent/IP. "Rotate" means the old refresh token is invalidated and a new pair issued — this is refresh-token rotation, a security best practice.
- **`POST /logout`** → `MessageResponse`. This one takes `CurrentPrincipal`, so it requires a valid access token. It revokes the given refresh session and passes `actor_id`/`org_id` from the principal for audit scoping.
- **`GET /me`** → `UserResponse`. Takes `CurrentUser` and returns it. This is the canonical "validate my token and tell me my profile" call the frontend makes on load.

Because `login`/`refresh`/`register` do **not** depend on `CurrentUser`, they are public; `logout`/`me` are protected simply by naming `CurrentPrincipal`/`CurrentUser` in their signatures.

**Connects to**: `AuthServiceDep`, `CurrentPrincipal`, `CurrentUser`, `client_ip` from `deps.py`; DTOs (`LoginRequest`, `TokenResponse`, `UserResponse`, etc.) from `app.schemas.auth`; `MessageResponse` from `app.schemas.common`; `IssuedToken` from `app.core.security`; settings from `app.core.config`. The heavy lifting (password hashing, token minting, session rows) lives in `AuthService`, which `deps.get_auth_service` builds.

---

### `backend/app/api/v1/chat.py`

**Purpose**: The single most important endpoint — `POST /chat/messages` — which runs the LangGraph AI engine for one user turn and **streams** the answer back as Server-Sent Events (SSE): typing indicators, tokens, citations, the routing decision, and a final `done`. Router prefix `/chat`.

**How it works** — this file has a subtle but very deliberate design, spelled out in its docstring:

The DB session is created **inside** the stream generator, *not* via the normal `SessionDep`. Why? A request-scoped session (from `get_session`) is torn down the moment the route function returns the `StreamingResponse` object — but at that point the body hasn't streamed yet. So a request-scoped session would already be closed when the generator tries to write assistant turns. The fix is to open a fresh session inside the generator and keep it alive for the whole stream.

Small helper:
```python
def _parse_decision(value: str | None) -> Decision | None:
    try:
        return Decision(value) if value else None
    except ValueError:
        return None
```
Safely turns the engine's decision string into the `Decision` enum, tolerating unknown values.

The route function does the request-scoped prep synchronously first:
```python
trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4().hex
org_id = str(principal.org_id)
user_id = str(principal.user_id)
message = payload.message
try:
    conv_uuid = uuid.UUID(payload.thread_id) if payload.thread_id else uuid.uuid4()
except ValueError:
    conv_uuid = uuid.uuid4()
```
It reuses the middleware-set `trace_id` (or mints one), and resolves the conversation id from the client-supplied `thread_id`, generating a new UUID if none/invalid was given.

Then the async generator `event_stream()` opens its own session and does everything:
```python
async with SessionFactory() as session:
    deps = get_graph_deps(session)
    conversations = ConversationRepository(session)
    conversation = await conversations.get(conv_uuid)
    if conversation is None:
        conversation = await conversations.create(id=conv_uuid, org_id=..., user_id=..., status=ConversationStatus.ACTIVE)
    turn_id = await conversations.next_turn_id(conversation.id)
    prior_clarifications = await conversations.count_assistant_clarifications(conversation.id)
    await conversations.add_message(conversation_id=..., turn_id=turn_id, role=MessageRole.USER, content=message, trace_id=trace_id)
    await session.commit()
```
Notice it calls `get_graph_deps(session)` directly (not the FastAPI-injected version) because the session is the local one. It finds-or-creates the conversation, computes the next `turn_id`, and — a nice product detail — counts how many times the assistant has already asked for clarification (`prior_clarifications`). That count is carried into the engine so an unresolved thread eventually escalates to a ticket rather than asking forever. The user's message is persisted and committed *before* streaming, so it survives even if the stream later fails.

The core loop drives the engine and forwards each event as SSE while capturing the final values:
```python
async for event in engine.astream(deps=deps, thread_id=str(conversation.id),
        org_id=org_id, user_id=user_id, trace_id=trace_id, turn_id=turn_id,
        user_message=message, clarification_rounds=prior_clarifications):
    if event.type.value == "done":
        final_text = str(event.data.get("response_text", ""))
    elif event.type.value == "citations":
        final_citations = event.data.get("citations", []) or []
    elif event.type.value == "decision":
        final_decision = event.data.get("decision")
    yield event.to_sse()
```
Each `event.to_sse()` yields a properly framed SSE chunk to the client in real time; simultaneously the handler remembers the final answer text, its citations, and the decision so they can be saved afterward.

Error handling degrades gracefully to an SSE error frame instead of a broken connection or a 500:
```python
except Exception as exc:
    _logger.exception("Chat stream failed: %s", exc)
    yield 'event: error\ndata: {"type": "error", "data": {"message": "internal error"}}\n\n'
    return
```

After a successful stream, the assistant turn is persisted with its citations and parsed decision, and the conversation's "last message" timestamp is bumped:
```python
if final_text:
    await conversations.add_message(..., role=MessageRole.ASSISTANT, content=final_text,
        trace_id=trace_id, citations=final_citations or None, decision=_parse_decision(final_decision))
    await conversations.touch_last_message(conversation)
    await session.commit()
```
Persisting both sides is what makes the `conversations` history endpoints show a complete transcript.

Finally the response itself:
```python
return StreamingResponse(event_stream(), media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
```
`text/event-stream` is the SSE content type; `X-Accel-Buffering: no` tells nginx not to buffer, so tokens reach the browser as they're produced.

**Connects to**: `AiEngineDep` (the compiled singleton) and `get_graph_deps` from `deps.py`; `SessionFactory` from `app.db.session` (the manual session, the whole point of this file); `ConversationRepository` from `app.repositories.conversation_repo`; enums (`ConversationStatus`, `Decision`, `MessageRole`) from `app.core.constants`; the `ChatTurnRequest` DTO from `app.schemas.chat`. It is the HTTP front door to the entire `app.agents` LangGraph engine.

---

### `backend/app/api/v1/conversations.py`

**Purpose**: Read-only conversation history for the current user. Router prefix `/conversations`.

**How it works**:

- **`GET ""`** (i.e. `/conversations`) — lists the caller's conversations, paginated:
  ```python
  rows = await repo.list_for_user(principal.org_id, principal.user_id, limit=pagination.limit, offset=pagination.offset)
  total = await repo.count_for_org(principal.org_id, user_id=principal.user_id, deleted_at=None)
  return build_page([ConversationResponse.model_validate(r) for r in rows], total, pagination)
  ```
  Every query is scoped by both `org_id` and `user_id` — tenant isolation *and* ownership. `build_page` wraps the rows plus the total count into the standard `Page[...]` envelope so the frontend gets consistent pagination metadata.

- **`GET /{conversation_id}/messages`** — returns one conversation's transcript, but only if it belongs to the caller:
  ```python
  conversation = await repo.get_for_user(conversation_id, principal.user_id)
  if conversation is None:
      raise NotFoundError("Conversation not found.")
  messages = await repo.list_messages(conversation_id)
  return [MessageDTO.model_validate(m) for m in messages]
  ```
  The ownership check is folded into `get_for_user`; if it doesn't match, the route raises `NotFoundError` (a 404 rather than 403 — it doesn't reveal that the conversation exists for someone else).

**Connects to**: `CurrentPrincipal`, `PaginationDep`, `SessionDep` from `deps.py`; `ConversationRepository` from `app.repositories.conversation_repo`; `Page`/`build_page` from `app.schemas.common`; `ConversationResponse`/`MessageDTO` from `app.schemas.conversation`; `NotFoundError` from `app.core.exceptions`. It reads the exact rows that `chat.py` writes.

---

### `backend/app/api/v1/tickets.py`

**Purpose**: The ticketing surface: list, per-ticket stats/KPIs, ticket detail, the user↔engineer message thread, and an AI-drafted reply suggestion for engineers. Router prefix `/tickets`. The docstring notes the message thread cleverly reuses `ticket_events` rows of type `COMMENTED` so no new table was needed.

An access model runs through the whole file:
```python
_ENGINEER_ROLES = {RoleKey.SUPPORT_ENGINEER.value, RoleKey.SME_REVIEWER.value, RoleKey.ADMIN.value}

def _can_access(principal, ticket) -> bool:
    return getattr(principal, "role") in _ENGINEER_ROLES or (ticket.created_by_user_id == getattr(principal, "user_id"))
```
Engineers/SMEs/admins can see any ticket in the org; a plain end-user can only see tickets they created.

This file also defines its response DTOs inline (rather than in `app.schemas`), because they're specific to these endpoints: `TicketDetailResponse` (full ticket with `intake_fields`, confidence, assignment), `TicketMessageDTO` (one thread message), `PostMessageRequest` (`text`, 1–4000 chars), `TicketStats`, and `SuggestReplyResponse`.

**How it works** — endpoint by endpoint:

- **`GET ""`** — list tickets with role-aware filtering:
  ```python
  filters = {} if principal.role in _ENGINEER_ROLES else {"created_by_user_id": principal.user_id}
  rows = await repo.list_for_org(principal.org_id, limit=..., offset=..., **filters)
  total = await repo.count_for_org(principal.org_id, **filters)
  ```
  Engineers get an unfiltered org view; end-users get only their own. Returns the standard `Page[TicketResponse]`.

- **`GET /stats`** — KPIs, status/priority breakdowns, and a 7-day trend. It builds a reusable filter list and applies the same role rule:
  ```python
  base = [Ticket.org_id == principal.org_id, Ticket.deleted_at.is_(None)]
  if principal.role not in _ENGINEER_ROLES:
      base.append(Ticket.created_by_user_id == principal.user_id)
  ```
  Then two grouped aggregate queries produce `by_status` and `by_priority`:
  ```python
  status_rows = (await session.execute(select(Ticket.status, func.count()).where(*base).group_by(Ticket.status))).all()
  ```
  Derived KPIs: `total = sum(by_status.values())`, `resolved = resolved + closed`, `open = total - resolved`, `urgent` from the priority map. The 7-day trend groups by `func.date(created_at)` since 6 days ago, then fills every day of the last 7 (including zero-count days) so the frontend chart has no gaps:
  ```python
  daily = [{"date": (now - timedelta(days=6 - i)).date().isoformat(),
            "count": by_day.get(..., 0)} for i in range(7)]
  ```
  Note: this endpoint is declared *before* `/{ticket_id}` so the literal path `stats` isn't captured as a ticket UUID — route ordering matters here.

- **`GET /{ticket_id}`** — ticket detail, guarded by `_can_access`; if the ticket is missing *or* the caller can't access it, it raises `NotFoundError` (again, 404 rather than leaking existence).

- **`GET /{ticket_id}/messages`** — the conversation thread. It loads the ticket (with access check), then filters the event log down to comment events and maps each into a `TicketMessageDTO`:
  ```python
  for ev in await repo.list_events(ticket_id):
      if ev.event_type == TicketEventType.COMMENTED:
          p = ev.payload or {}
          out.append(TicketMessageDTO(id=ev.id, sender_role=p.get("sender_role", "user"),
              sender_email=p.get("sender_email"), text=p.get("text", ""), created_at=ev.created_at))
  ```
  The message content lives in the event's JSON `payload`, which is why no schema migration was needed.

- **`POST /{ticket_id}/messages`** — post a reply. After the access check, it derives the sender role, appends a `COMMENTED` event, and — a nice touch — creates an in-app notification for the *other* party:
  ```python
  sender_role = "engineer" if principal.role in _ENGINEER_ROLES else "user"
  ev = await repo.add_event(ticket_id=ticket_id, event_type=TicketEventType.COMMENTED,
      actor_user_id=principal.user_id,
      payload={"text": payload.text, "sender_role": sender_role, "sender_email": principal.email})
  recipient = ticket.assigned_engineer_id if sender_role == "user" else ticket.created_by_user_id
  if recipient and recipient != principal.user_id:
      session.add(Notification(org_id=..., recipient_user_id=recipient,
          channel=NotificationChannel.IN_APP, type=NotificationType.MENTION,
          status=NotificationStatus.SENT, ticket_id=ticket.id,
          payload={"title": "New message", "body": f"New message on '{ticket.subject}'."}))
  await session.commit()
  ```
  If a user writes, the assigned engineer is notified; if an engineer writes, the ticket creator is. The `recipient != principal.user_id` check avoids notifying yourself. Note the event insert and the notification are committed in one transaction, so they succeed or fail together.

- **`POST /{ticket_id}/suggest-reply`** — engineer-only (guarded by `dependencies=[Depends(require_roles(SUPPORT_ENGINEER, SME_REVIEWER, ADMIN))]`) AI draft. It loads the ticket, resolves the KB retrieval namespace for the ticket's category, and retrieves grounding context — degrading gracefully if retrieval fails:
  ```python
  namespace = get_category_registry().get(ticket.category).retrieval_namespace
  try:
      outcome = await deps.retriever.retrieve(query=ticket.subject, org_id=str(principal.org_id),
          namespace=namespace, category=ticket.category)
      context = outcome.context or "(no matching knowledge-base articles)"
  except Exception:
      context = "(no matching knowledge-base articles)"
  ```
  It then builds a system+user prompt (senior-engineer persona, use the SOURCES, 3–6 sentences, no invented details), calls the large LLM, and returns the trimmed draft:
  ```python
  result = await deps.llm_large.generate(messages)
  return SuggestReplyResponse(suggestion=result.text.strip())
  ```
  This is a RAG (retrieval-augmented generation) call reusing the same retriever/LLM the chat engine uses — but assembled here directly via `get_graph_deps(session)` rather than the full graph.

**Connects to**: `CurrentPrincipal`, `PaginationDep`, `SessionDep`, `get_graph_deps`, `require_roles` from `deps.py`; `TicketRepository` from `app.repositories.ticket_repo`; ORM models `Ticket` (`app.models.ticket`) and `Notification` (`app.models.ops`); enums/`RoleKey` from `app.core.constants`; `ChatMessage` from `app.providers.base`; the category registry from `app.registries.category_registry`; `TicketResponse` and `Page`/`build_page` from schemas. It's the only router besides `chat.py` that reaches into the AI stack.

---

### `backend/app/api/v1/kb.py`

**Purpose**: The Knowledge Base module — full CRUD plus versioning and file ingestion. Reading is open to everyone (published only); creating/editing/publishing is gated behind the `KB_WRITE` permission (SME + Admin). Router prefix `/kb`. The docstring notes SME/Admin additionally see drafts.

Two inline DTOs matter: `KnowledgeDocumentDetail` (a document with its assembled `body`), `KbCreateRequest`/`KbEditRequest` (write payloads), and `KbVersionDTO` (a history entry). `_EDITOR_ROLES = {SME_REVIEWER, ADMIN}` gates draft visibility.

**How it works**:

- **`GET /documents`** — role-aware list/search. Non-editors are silently restricted to published docs:
  ```python
  statuses = None if principal.role in _EDITOR_ROLES else [DocStatus.PUBLISHED]
  rows = await repo.search_documents(principal.org_id, q=q, category=category, statuses=statuses, limit=..., offset=...)
  ```
  Supports `?q=` title search and `?category=` filters; returns `Page[KnowledgeDocumentResponse]`.

- **`GET /documents/{doc_id}`** — full document with body. It reconstructs the body by joining the document's chunks:
  ```python
  if doc is None or (doc.doc_status != DocStatus.PUBLISHED and principal.role not in _EDITOR_ROLES):
      raise NotFoundError(...)
  chunks = await repo.list_chunks(doc.id)
  body = "\n\n".join(c.text for c in chunks)
  ```
  Non-editors can't fetch a draft (404). Documents are stored as retrievable *chunks*, and the body is those chunks re-joined.

- **`POST /documents/create`** — create a Markdown article, gated by `require_permissions(Permission.KB_WRITE)`, returns **201**. It validates the category against the registry (falling back to `application_error`), resolves the namespace, then writes three linked records in one transaction: the document, a single chunk holding the body, and a version-1 history snapshot:
  ```python
  doc = await repo.create(..., doc_status=DocStatus.DRAFT, version=1,
      checksum=hashlib.sha256(payload.body.encode()).hexdigest(), ...)
  await session.flush()
  await repo.add_chunk(chunk_id=uuid.uuid4(), doc_id=doc.id, ..., chunk_index=0, text=payload.body, ...)
  await repo.add_version(doc_id=doc.id, version=1, ..., change_summary="Created via KB module", ...)
  await session.commit()
  ```
  New articles start as **draft** (not searchable by end-users until published). The `flush()` assigns `doc.id` before the dependent rows reference it. The checksum lets the system detect content changes.

- **`PATCH /documents/{doc_id}`** — edit, `KB_WRITE`-gated, with automatic versioning:
  ```python
  new_version = (doc.version or 1) + 1
  if payload.title: doc.title = payload.title
  if payload.body is not None:
      chunks = await repo.list_chunks(doc.id)
      if chunks:
          chunks[0].text = payload.body
          chunks[0].version = new_version
      else:
          await repo.add_chunk(...)
      doc.checksum = hashlib.sha256(payload.body.encode()).hexdigest()
  doc.version = new_version
  await repo.add_version(doc_id=doc.id, version=new_version, ..., change_summary="Edited via KB module", ...)
  ```
  Every edit bumps the version and appends a history snapshot, so you get an audit trail of changes. It updates the existing first chunk in place (or creates one if somehow missing).

- **`GET /documents/{doc_id}/versions`** — returns the version history as `list[KbVersionDTO]`.

- **Publish / unpublish** — both `KB_WRITE`-gated and both delegate to the shared helper:
  ```python
  async def _set_status(session, principal, doc_id, new_status) -> KnowledgeDocumentDetail:
      doc.doc_status = new_status
      if new_status == DocStatus.PUBLISHED:
          doc.last_verified_at = datetime.now(timezone.utc)
      await session.execute(update(KbChunk).where(KbChunk.doc_id == doc.id).values(doc_status=new_status))
      await session.commit()
  ```
  Crucially it updates the status on **both** the document *and* all its chunks (a bulk `UPDATE`), because retrieval filters on chunk status — otherwise a "published" doc would still be invisible to search. Publishing also stamps `last_verified_at`. `/publish` sets `PUBLISHED`, `/unpublish` sets `DRAFT`.

- **`POST /documents`** — upload a PDF/DOCX/text file for ingestion, `KB_WRITE`-gated, returns **202 Accepted**. It parses the bytes synchronously to fail fast on unreadable files, then hands the extracted text to a Celery task and returns immediately:
  ```python
  data = await file.read()
  text = parse_document(data, filename=file.filename, content_type=file.content_type)
  namespace = get_category_registry().get(category).retrieval_namespace
  ingest_document.delay(org_id=..., created_by_user_id=..., title=..., category=category, namespace=namespace, text=text)
  return MessageResponse(detail="Document accepted for ingestion.")
  ```
  `202` + "accepted for ingestion" is the correct async-work contract — chunking and embedding happen in the background worker, not in the request.

**Connects to**: `CurrentPrincipal`, `PaginationDep`, `SessionDep`, `require_permissions` from `deps.py`; `Permission.KB_WRITE` from `app.core.rbac`; `KnowledgeRepository` (`app.repositories.kb_repo`) and the `KbChunk` model (`app.models.knowledge`); `DocStatus`/`SourceType`/`RoleKey` from `app.core.constants`; `parse_document` from `app.rag.parsers`; the category registry; and the `ingest_document` Celery task from `app.workers.ingestion_tasks`. The docs it publishes are exactly what the chat engine's retriever later searches.

---

### `backend/app/api/v1/analytics.py`

**Purpose**: Reporting endpoint for dashboards. Router prefix `/analytics`.

**How it works**: A single route:
```python
@router.get("/summary", dependencies=[Depends(require_permissions(Permission.ANALYTICS_READ))], ...)
async def summary(principal, session) -> AnalyticsSummary:
    repo = AnalyticsRepository(session)
    counts = await repo.counts_grouped_by_type(principal.org_id)
    return AnalyticsSummary(counts=counts)
```
It's gated by the `ANALYTICS_READ` permission and scoped to the caller's org. The repository does a grouped count of analytics events by type; the route just wraps the result in `AnalyticsSummary`. Small and declarative — the interesting SQL lives in the repository.

**Connects to**: `CurrentPrincipal`, `SessionDep`, `require_permissions` from `deps.py`; `Permission.ANALYTICS_READ` from `app.core.rbac`; `AnalyticsRepository` from `app.repositories.analytics_repo`; `AnalyticsSummary` from `app.schemas.analytics`.

---

### `backend/app/api/v1/notifications.py`

**Purpose**: The in-app notification center — list, unread badge count, mark-one-read, mark-all-read. Router prefix `/notifications`.

**How it works**:

- **`GET ""`** — paginated list of the caller's notifications, scoped by `org_id` + `recipient_user_id`; returns `Page[NotificationResponse]`.
- **`GET /unread-count`** — powers the bell badge:
  ```python
  return {"count": await repo.count_unread(principal.org_id, principal.user_id)}
  ```
- **`POST /read-all`** — marks everything read. It fetches up to 200 unread rows and marks each, then commits once:
  ```python
  rows = await repo.list_for_user(principal.org_id, principal.user_id, unread_only=True, limit=200)
  for n in rows:
      await repo.mark_read(n)
  await session.commit()
  return MessageResponse(detail=f"Marked {len(rows)} read.")
  ```
- **`POST /{notification_id}/read`** — marks one, with an org-scoped existence check that 404s if it doesn't belong to the caller's org:
  ```python
  notification = await repo.get_for_org(notification_id, principal.org_id)
  if notification is None:
      raise NotFoundError("Notification not found.")
  await repo.mark_read(notification)
  ```

Note the `mark_read` on a single notification relies on the repo to persist; the `read-all` path commits explicitly after the loop.

**Connects to**: `CurrentPrincipal`, `PaginationDep`, `SessionDep` from `deps.py`; `NotificationRepository` from `app.repositories.notification_repo`; `NotificationResponse` and the common `Page`/`build_page`/`MessageResponse` schemas. These are the very rows written by `tickets.py`'s `post_message`.

---

### `backend/app/api/v1/feedback.py`

**Purpose**: Collect thumbs-up/down (and comments) on AI answers. Router prefix `/feedback`.

**How it works**: One route, returning **201**:
```python
async def submit_feedback(payload: FeedbackRequest, principal, session) -> MessageResponse:
    service = FeedbackService(FeedbackRepository(session))
    await service.submit(org_id=principal.org_id, user_id=principal.user_id,
        conversation_id=payload.conversation_id, rating=payload.rating,
        message_id=payload.message_id, ticket_id=payload.ticket_id,
        comment=payload.comment, feedback_handle=payload.feedback_handle)
    return MessageResponse(detail="Thanks for your feedback.")
```
Identity (`org_id`, `user_id`) is taken from the principal, never the body; the rest (which conversation/message/ticket, rating, comment, and a `feedback_handle`) comes from `FeedbackRequest`. The route builds the service inline and delegates all logic to `FeedbackService.submit`. The `feedback_handle` is how a specific AI answer is tied back to its feedback.

**Connects to**: `CurrentPrincipal`, `SessionDep` from `deps.py`; `FeedbackService` (`app.services.feedback_service`) over `FeedbackRepository` (`app.repositories.feedback_repo`); `FeedbackRequest` (`app.schemas.feedback`) and `MessageResponse`.

---

### `backend/app/api/v1/ai_data.py`

**Purpose**: A natural-language database API. `POST /ai/query {"instruction": "..."}` lets the LLM choose a data operation, the service runs it against Postgres, and the LLM explains the result. The docstring frames it as "the LLM doing 100% of the interpretation." Router prefix `/ai`.

**How it works**:

- **`GET /tools`** — introspection; returns the catalog of operations the AI is allowed to perform:
  ```python
  return {"tools": TOOLS}
  ```
  `TOOLS` is a static definition imported from the service. This lets a UI show users what's possible.

- **`POST /ai/query`** — the workhorse. Payload is validated to a non-empty instruction ≤1000 chars (`AiQueryRequest`). It builds the service with the **small** LLM and runs, passing the `principal` so the service can scope/authorize every operation to the caller's tenant:
  ```python
  service = AiDataApi(session, get_llm_provider("small"))
  return await service.run(payload.instruction, principal)
  ```
  Passing `principal` (not raw ids) into `service.run` is the safety boundary: the LLM proposes an operation, but the service constrains it to what this principal is allowed to touch.

**Connects to**: `CurrentPrincipal`, `SessionDep` from `deps.py`; `get_llm_provider` from `app.providers.registry`; `TOOLS` and `AiDataApi` from `app.services.ai_data_api`. The `small` model is used deliberately — this is a routing/tool-selection task, not deep reasoning.

---

### `backend/app/api/v1/docsearch.py`

**Purpose**: A per-user "document intelligence" feature — attach files or public URLs, index them, then AI-search across your own attached sources. Router prefix `/docsearch`. The docstring lays out the full flow: inspect → upload/url → list/delete → search. This is the user-facing counterpart to the `uploads=UploadsSearcher(...)` field in `get_graph_deps`, so the chat engine can also draw on these attachments.

Guards and constants: `_MAX_BYTES = 20 MB`; request DTOs `UrlRequest` and `SearchRequest`; and a small file-type sniffer:
```python
def _kind(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"): return "pdf"
    if name.endswith(".docx"): return "docx"
    if name.endswith((".xlsx", ".xlsm")): return "excel"
    return "text"
```

**How it works**:

- **`POST /inspect`** (multipart) — pre-flight for Excel. It reads the bytes, enforces the size cap, and if the file is a spreadsheet, returns the sheet names so the UI can ask "which tab?":
  ```python
  if len(data) > _MAX_BYTES: raise ValidationError("File is larger than 20 MB.")
  kind = _kind(file.filename or "")
  if kind == "excel":
      try: sheets = ds.sheet_names(data)
      except Exception as exc: raise ValidationError(f"Could not read the Excel file: {exc}") from exc
  return {"filename": file.filename, "source_type": kind, "sheets": sheets}
  ```
  Notably `/inspect` takes no `SessionDep` — it's pure parsing, nothing is persisted.

- **`POST /upload`** (multipart file [+ optional `sheet`]) — parse then index. It dispatches to the right parser by kind (each imported as a private helper from the service module), converting failures into a clean `ValidationError`, and rejects empty extractions:
  ```python
  if kind == "pdf": passages = ds._parse_pdf(data)
  elif kind == "docx": passages = ds._parse_docx(data)
  elif kind == "excel": chosen_sheet, passages = ds._parse_excel(data, sheet)
  else: passages = ds._parse_text(data)
  ...
  if not passages: raise ValidationError("No readable text was found in that file.")
  svc = DocSearchService(session, get_llm_provider("small"))
  return await svc.ingest(principal, filename=..., source_type=kind, passages=passages, sheet=chosen_sheet)
  ```
  "Passages" are the chunk-like text units the searcher indexes.

- **`POST /url`** — same idea for a public web page: `ds.fetch_url(payload.url)` produces passages, which are then ingested with `source_type="url"` and the URL retained as `source_ref`. Fetch failures become `ValidationError`.

- **`GET /documents`** — lists the caller's own attached sources (`svc.list_documents(principal)`).

- **`DELETE /documents/{doc_id}`** — removes one, 404-ing if it isn't the caller's:
  ```python
  if not await svc.delete_document(principal, doc_id):
      raise NotFoundError("Document not found.")
  return {"deleted": True}
  ```

- **`POST /search`** — the payoff: search the caller's attached files and let the LLM summarize the hits (with file, location, and verbatim snippets, per the docstring):
  ```python
  svc = DocSearchService(session, get_llm_provider("small"))
  return await svc.search(principal, payload.query)
  ```

Every ingest/search path builds `DocSearchService(session, get_llm_provider("small"))` and passes `principal`, so all indexing and retrieval is scoped to the current user.

**Connects to**: `CurrentPrincipal`, `SessionDep` from `deps.py`; `get_llm_provider` from `app.providers.registry`; the `docsearch_service` module (both the free functions `sheet_names`/`fetch_url`/`_parse_*` and the `DocSearchService` class); `ValidationError`/`NotFoundError` from `app.core.exceptions`. It shares the `UploadsSearcher`/docsearch backend that `deps.get_graph_deps` injects into the AI engine, which is what lets the chat assistant answer from a user's attached documents.

---

### How the API layer fits together (recap)

1. A request arrives; middleware sets a `trace_id`.
2. `router.py` routes it to a v1 endpoint under the version prefix (`health.py` sits at the root, outside v1).
3. FastAPI resolves the endpoint's dependencies from `deps.py`: it opens a request-scoped DB session, verifies the JWT into a `User`/`Principal`, enforces `require_roles`/`require_permissions`, and injects services, pagination, or the AI-engine bundle as needed.
4. The route runs — thin: it validates input via schemas, calls a repository/service (or, for `chat.py`, streams the LangGraph engine), and returns a DTO or a `Page[...]`.
5. If anything raises an `AppError` (or validation/HTTP/unexpected error), `errors.py` converts it into an RFC 7807 `application/problem+json` response stamped with the `trace_id`, so the client always gets a consistent, correlatable error.

The consistent throughline: **identity and authorization always come from the verified token via `deps.py`, never from the request body; every query is scoped by `org_id` (tenant) and often by `user_id` (ownership); routers stay thin and delegate real work to repositories and services.**

---

I have read all the files. Now I'll produce the walkthrough.

## Frontend (React SPA)

This is a Vite + React + TypeScript single-page app. It uses **React Router** for navigation, **TanStack React Query** for server state (caching, refetching, mutations), **Zustand** for small pieces of client state (auth tokens, UI toasts, theme), **Axios** for HTTP, and **Tailwind CSS** for styling. The code is organized into three big buckets:

- `shared/` — reusable plumbing: the API client, the React Query setup, the Zustand stores, small hooks, and a library of UI "primitive" components.
- `layout/` and `router/` — the application shell (sidebar, top bar) and the route table plus the auth/role guards.
- `modules/` — one folder per feature (auth, chat, tickets, knowledge-base, etc.). Each module typically has an `api.ts` (the network calls) and one or more `*.tsx` page/component files.

A key convention throughout: imports use the `@/` alias, which points at `frontend/src`. So `@/shared/ui/Button` means `frontend/src/shared/ui/Button.tsx`.

---

### Entry point and app shell

#### `frontend/src/main.tsx`

**Purpose:** The very first file that runs. It boots React, wires in the global providers, and mounts everything into the page.

**How it works:**
- It imports the root `App` component, the shared `queryClient`, and the `useThemeStore`.
- Line 10 is subtle and important: `useThemeStore.getState().apply();` runs *before* React renders. `getState()` reads the Zustand store outside of React, and `apply()` adds or removes the `dark` class on `<html>` based on the saved theme. Doing this synchronously before the first paint prevents a "flash of light theme" when a dark-mode user reloads.
- Lines 12–18 mount the app: `ReactDOM.createRoot(...).render(...)` renders the tree wrapped in two providers:
  - `<React.StrictMode>` — a development-only helper that double-invokes some functions to surface bugs.
  - `<QueryClientProvider client={queryClient}>` — makes React Query available to every component so any component can call `useQuery`/`useMutation`.
- `import "@/index.css";` pulls in the Tailwind directives and base styles.

**Connects to:** renders `App` (`App.tsx`); uses `queryClient` (`shared/api/queryClient.ts`) and `themeStore` (`shared/store/themeStore.ts`).

#### `frontend/src/App.tsx`

**Purpose:** The root component. It sets up client-side routing and mounts the global toast layer.

**How it works:** It wraps everything in `<BrowserRouter>` (React Router's provider that reads and writes the browser URL), then renders `<AppRoutes />` (the route table) and `<ToastContainer />`. Because `ToastContainer` sits at the top level alongside the routes, toast pop-ups can appear over any page.

**Connects to:** `AppRoutes` (`router/routes.tsx`) and `ToastContainer` (`shared/ui/ToastContainer.tsx`).

#### `frontend/src/index.css`

**Purpose:** Global stylesheet and Tailwind entry.

**How it works:** The three `@tailwind base/components/utilities` directives are where Tailwind injects its generated CSS. `:root { color-scheme: light dark; }` tells the browser the page supports both schemes (so native form controls and scrollbars adapt). It forces `html, body, #root` to full height so the flex-based full-screen layout works, and applies base body styling via `@apply bg-slate-50 text-slate-900 antialiased`.

**Connects to:** imported by `main.tsx`; the utility classes it enables are used by every component.

#### `frontend/src/vite-env.d.ts`

**Purpose:** TypeScript type declarations for Vite's environment variables.

**How it works:** It references Vite's client types and declares that `import.meta.env` may have an optional `VITE_API_BASE_URL` string. This is what makes `import.meta.env.VITE_API_BASE_URL` type-safe in `client.ts`.

**Connects to:** consumed anywhere `import.meta.env` is read, primarily `shared/api/client.ts`.

---

### The application layout

#### `frontend/src/layout/AppLayout.tsx`

**Purpose:** The shell that wraps every *authenticated* page — the sidebar on the left, the top bar on top, and the page content in the middle.

**How it works:** It renders a full-height flex row. `<Sidebar />` is the fixed left column; the rest is a vertical flex column holding `<Topbar />` and a scrollable `<main>`. The crucial piece is `<Outlet />` inside `<main>` — this is React Router's placeholder where the matched child route (Dashboard, Chat, Tickets, etc.) gets rendered. `overflow-hidden` on the outer containers plus `overflow-y-auto` on `<main>` means only the content area scrolls, while the sidebar and top bar stay put.

**Connects to:** used by `router/routes.tsx` as the element wrapped in `ProtectedRoute`; renders `Sidebar` and `Topbar`.

#### `frontend/src/layout/Sidebar.tsx`

**Purpose:** The left navigation menu, with links grouped into sections and filtered by the user's role.

**How it works:**
- It defines two data structures, `NavItem` (a link with an optional `roles` allow-list) and `NavSection` (a titled group of items).
- `STAFF` is the list of privileged roles: `["support_engineer", "sme_reviewer", "admin"]`.
- `SECTIONS` is the actual menu: a "Workspace" section everyone sees (Home, AI Chat, My Tickets, Notifications, Profile) and a "Tools" section whose items each carry a `roles` restriction (Knowledge Base / Document Search / AI Data API for staff, Analytics / Admin for admins only).
- Inside the component, `const { user } = useAuth();` gets the current user, and `role` defaults to `"end_user"` if none. The helper `visible(items)` filters out any item whose `roles` list doesn't include the current role:
  ```ts
  const visible = (items) => items.filter((i) => !i.roles || i.roles.includes(role));
  ```
- When rendering, it maps over `SECTIONS`, and `if (items.length === 0) return null;` hides an entire section header when a role can see none of its items — so an end user never even sees the "Tools" title.
- Each link is a `<NavLink>`. The `end={item.to === "/"}` prop matters: without `end`, the Home link (`/`) would be considered "active" on every route because every path starts with `/`. The `className` is a function of `isActive`, applying a highlighted style to the current page.

**Connects to:** reads the user via `useAuth` (`modules/auth/useAuth.ts`); its role lists mirror the route guards in `router/routes.tsx`. Rendered by `AppLayout`.

#### `frontend/src/layout/Topbar.tsx`

**Purpose:** The header bar: a greeting, the notification bell with an unread badge, a theme toggle, the user's email/role, and a sign-out button.

**How it works:**
- `ROLE_LABEL` maps internal role codes to friendly display strings ("admin" → "Administrator").
- `const { user, logout } = useAuth();` and `useNavigate()` give it identity and routing. `toggleTheme = useThemeStore((s) => s.toggle)` grabs just the theme toggle action.
- **The bell's unread count** is a polling query:
  ```ts
  const { data: unread = 0 } = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: notificationsApi.unreadCount,
    refetchInterval: 20000,
    enabled: Boolean(user),
  });
  ```
  `refetchInterval: 20000` re-fetches every 20 seconds so the badge stays fresh; `enabled: Boolean(user)` stops it from firing before login. `data: unread = 0` defaults the count to 0 while loading.
- `onLogout` awaits `logout()` then navigates to `/login` with `replace: true` (so Back won't return to the app).
- `firstName` is derived from the email local-part; `roleLabel` from the map.
- The bell button navigates to `/notifications`; when `unread > 0` it shows a red badge that displays `"9+"` for anything above 9. There is a "Theme" ghost button, the email + a role `Badge`, and a "Sign out" button.

**Connects to:** `useAuth`, `notificationsApi` (`modules/notifications/api.ts`), `themeStore`, and the `Badge`/`Button` primitives.

---

### The router and guards

#### `frontend/src/router/routes.tsx`

**Purpose:** The central route table — which URL renders which page, and which pages require login or a specific role.

**How it works:**
- `/login` and `/register` are public routes rendered without the shell.
- The block starting at line 26 is a **layout route**: it has no `path`, just an `element` of `<ProtectedRoute><AppLayout /></ProtectedRoute>`. Every child route nested inside it therefore (a) is gated by `ProtectedRoute`, and (b) renders inside `AppLayout`'s `<Outlet />`.
- Child routes: `/` → Dashboard, `/chat` → Chat, `/tickets` and `/tickets/:id` → the ticket list and detail, `/profile`, `/notifications`.
- Staff/admin pages are additionally wrapped in `<RoleRoute roles={...}>`:
  - `/kb`, `/ai-data`, `/docsearch` use `KB_ROLES = ["support_engineer", "sme_reviewer", "admin"]`.
  - `/analytics` and `/admin` use `["admin"]`.
- The final `<Route path="*" element={<Navigate to="/" replace />} />` is a catch-all: any unknown URL redirects to the dashboard.

**Connects to:** imports every page component and the two guards; wrapped `AppLayout` provides the shell. The role arrays here duplicate the intent of `Sidebar`'s lists.

#### `frontend/src/router/guards.tsx`

**Purpose:** Two small components that block access based on authentication and role.

**How it works:**
- `ProtectedRoute` reads `selectIsAuthenticated` from the auth store. If not logged in, it returns `<Navigate to="/login" state={{ from: location }} replace />` — redirecting to login and stashing the attempted location in router state (so the app could return there after login). Otherwise it renders its `children`.
- `RoleRoute` reads `user` and checks `roles.includes(user.role)`. If there's no user or the role isn't allowed, it redirects to `/` (the dashboard); otherwise it renders `children`. This is a UI-level guard only — the backend must still enforce authorization, but it keeps users out of pages they can't use.

**Connects to:** the auth store (`shared/store/authStore.ts`); used by `routes.tsx`.

---

### Shared API layer

#### `frontend/src/shared/api/client.ts`

**Purpose:** The single configured Axios instance every module uses. It attaches the JWT to requests and transparently refreshes an expired token on a 401.

**How it works:**
- `API_BASE_URL` is `import.meta.env.VITE_API_BASE_URL ?? "/api/v1"` — configurable per environment, defaulting to a relative path (so a dev proxy or same-origin deploy works out of the box).
- `apiClient` is created with that base URL and a JSON content-type.
- **Request interceptor** (lines 18–24): before every request it reads `useAuthStore.getState().accessToken` and, if present, sets `Authorization: Bearer <token>`. Note it uses `getState()` (not a hook) because interceptors run outside React.
- **Single-flight refresh** (lines 26–46): `refreshPromise` is a module-level variable that de-duplicates refreshes. `refreshAccessToken()` reads the refresh token; if there's none it returns `null`. Otherwise it calls `POST /auth/refresh` using the *bare* `axios` (not `apiClient`) to avoid recursively triggering the interceptors, stores the new pair via `setTokens`, and returns the new access token. On failure it calls `clear()` (logs the user out) and returns `null`.
- **Response interceptor** (lines 48–66): success passes through. On error it inspects the config and status. The refresh only triggers when:
  ```ts
  status === 401 && original && !original._retried && !original.url?.includes("/auth/")
  ```
  i.e. a 401, not already retried, and not itself an auth call. It marks `original._retried = true` (so a request is retried at most once), reuses or creates `refreshPromise` so many concurrent 401s share *one* refresh call, awaits the new token, and if it got one, sets the new Authorization header and replays the original request via `apiClient(original)`. If refresh failed, the original error is rejected.
- `problemMessage(error)` (lines 68–74) turns an error into a human string. For Axios errors it prefers the backend's RFC-7807 `detail`, then `title`, then the generic message; otherwise it falls back to `error.message`.

**Connects to:** the auth store (for tokens); the `ProblemDetail`/`TokenResponse` types. Imported by every module's `api.ts`, and `problemMessage` is used in `onError` handlers across the app.

#### `frontend/src/shared/api/queryClient.ts`

**Purpose:** Configures the global React Query client's defaults.

**How it works:** `staleTime: 30_000` means data is considered fresh for 30 seconds (no automatic refetch in that window). `retry: 1` retries a failed query once; `refetchOnWindowFocus: false` disables the default "refetch when you tab back" behavior. Mutations use `retry: 0` (never auto-retry a write, which could double-submit).

**Connects to:** instantiated in `main.tsx` and imported directly (as `queryClient`) wherever code needs to invalidate or clear the cache (e.g. `useAuth`, ticket/KB/notification pages).

#### `frontend/src/shared/api/queryKeys.ts`

**Purpose:** A single source of truth for React Query cache keys, so fetching and invalidation always use the same key shape.

**How it works:** It's an object of `as const` arrays and key-builder functions: `me`, `conversations(page)`, `conversation(id)`, `messages(conversationId)`, `tickets(queue, page)`, `ticket(id)`, `knowledge(page)`, `notifications(page)`, and `analytics`. Centralizing keys avoids typos and makes targeted invalidation reliable.

**Connects to:** used by the dashboard, tickets, notifications, and analytics pages. (Note: several pages also inline their own array keys like `["ticket", id, "messages"]` or `["knowledge", page, q]` rather than always going through this file.)

#### `frontend/src/shared/api/sse.ts`

**Purpose:** Streams the AI chat response token-by-token over Server-Sent Events (SSE). This is the beating heart of the "typing" chat effect.

**How it works:**
- The file's doc comment explains *why* it's hand-rolled: the browser's `EventSource` only does GET, but the chat endpoint is an authenticated POST, so it uses `fetch` + a manual stream reader instead.
- `StreamEvent` is a parsed frame (`type`, `data`, optional `index`); `StreamHandlers` carries `onEvent`, optional `onError`, and an optional `AbortSignal`.
- `streamChat(body, handlers)`:
  - Reads the access token from the store and POSTs to `${API_BASE_URL}/chat/messages` with `Accept: text/event-stream` and the Bearer header, passing the `signal` for cancellation.
  - If `!response.ok || !response.body` it throws.
  - It reads the response body with `response.body.getReader()` and decodes chunks with a `TextDecoder`. The `while (true)` loop appends decoded text to `buffer`, then repeatedly looks for the SSE frame separator `"\n\n"`. Each complete frame is sliced out, parsed by `parseFrame`, and passed to `onEvent`. Partial data stays in `buffer` for the next read.
  - `catch`: an `AbortError` (user hit Stop) is swallowed silently; any other error goes to `onError`.
- `parseFrame(frame)` keeps only the `data:` lines, strips the `data:` prefix, joins them, and `JSON.parse`s the result into `{ type, data, index }`, defaulting `type` to `"message"`. Malformed frames return `null` and are ignored.

**Connects to:** `API_BASE_URL` and the auth store; consumed by `modules/chat/ChatPage.tsx`.

#### `frontend/src/shared/api/types.ts`

**Purpose:** TypeScript interfaces mirroring the backend's Pydantic response schemas — the shared contract between front and back.

**How it works:** It declares DTOs used across modules: `UserResponse`, `TokenResponse`, `LoginRequest`/`RegisterRequest`, `MessageResponse`, `ProblemDetail` (the RFC-7807 error shape with `title`, `status`, `detail`, `trace_id`, `errors`), the generic pagination pair `PageMeta` and `Page<T>`, plus domain types `Conversation`, `ChatMessageDTO`, `Citation`, `Ticket`, `NotificationDTO`, and `KnowledgeDocument`. Note `Page<T>` is generic, so `Page<Ticket>` and `Page<NotificationDTO>` reuse one definition.

**Connects to:** imported by `client.ts` and virtually every module `api.ts`; module-specific detail types (e.g. `TicketDetail`) extend these base interfaces.

---

### Shared state stores (Zustand)

#### `frontend/src/shared/store/authStore.ts`

**Purpose:** Holds authentication state — tokens, the current user, the org slug — and persists it across page reloads.

**How it works:** `create<AuthState>()(persist(...))` wraps the store in Zustand's `persist` middleware. State: `accessToken`, `refreshToken`, `user`, `orgSlug`, plus setters `setTokens`, `setUser`, `setOrgSlug`, and `clear` (which nulls tokens and user on logout). The `persist` config names the localStorage key `"helpdesk-auth"` and uses `partialize` to choose exactly which fields get saved. `selectIsAuthenticated` is an exported selector: `Boolean(state.accessToken)` — used by the route guard. Because the store is a plain object accessible via `getState()`, non-React code (the Axios interceptors, the SSE helper) can read the token too.

**Connects to:** read/written by `client.ts`, `sse.ts`, `useAuth`, `guards.tsx`. Persisted so a refresh keeps you logged in.

#### `frontend/src/shared/store/uiStore.ts`

**Purpose:** Ephemeral UI state — the toast queue and a sidebar-open flag — plus a tiny helper for firing toasts from anywhere.

**How it works:** `Toast` has an `id`, a `kind` ("success" | "error" | "info"), and a `message`. A module-level `toastId` counter guarantees unique ids. The store exposes `pushToast` (append), `dismissToast` (filter out by id), and `toggleSidebar`. The exported `toast` object is the convenience API used everywhere: `toast.success(...)`, `toast.error(...)`, `toast.info(...)` — each calls `useUiStore.getState().pushToast(...)`, so you can raise a toast from non-component code (like a mutation's `onError`).

**Connects to:** rendered by `ToastContainer`; `toast.*` is called across nearly every module (auth, tickets, KB, docsearch, ai-data, feedback).

#### `frontend/src/shared/store/themeStore.ts`

**Purpose:** Light/dark theme, persisted, applied to the `<html>` element.

**How it works:** State holds `theme` ("light" by default). `toggle()` computes the opposite theme, stores it, and toggles the `dark` class on `document.documentElement`. `apply()` just syncs the class to the current stored value (called once at startup in `main.tsx`). Persisted under `"helpdesk-theme"`.

**Connects to:** `main.tsx` calls `apply()`; `Topbar` calls `toggle()`. (Tailwind's dark-mode variants react to the `dark` class.)

#### `frontend/src/shared/hooks/usePagination.ts`

**Purpose:** Encapsulates 1-based page state and next/prev controls for list pages.

**How it works:** It holds `page` (starting at 1) and a fixed `size` (default 20). `next()` increments; `prev()` decrements but never below 1 (`Math.max(1, p - 1)`); `setPage` jumps directly. It returns `{ page, size, next, prev, setPage }`.

**Connects to:** used by `TicketsPage`, `NotificationsPage`, and `KnowledgeBasePage` to drive their paginated queries.

---

### Shared UI primitives

These small, presentational components give the app a consistent look. `shared/ui/index.ts` re-exports all of them so pages can import from one place.

#### `frontend/src/shared/ui/Button.tsx`

**Purpose:** The standard button, with variants, sizes, and a loading spinner.

**How it works:** It extends native `ButtonHTMLAttributes`, adding `variant` ("primary" | "secondary" | "ghost" | "danger"), `size` ("sm" | "md"), and `loading`. Two lookup maps (`VARIANTS`, `SIZES`) translate those props into Tailwind class strings, concatenated with a shared base. `disabled={disabled || loading}` means a loading button is also unclickable, and when `loading` it renders an animated spinner span before the children. Unknown props (`...rest`) spread onto the `<button>`, so `onClick`, `type`, etc. all work.

**Connects to:** used by essentially every page and modal.

#### `frontend/src/shared/ui/Input.tsx`

**Purpose:** A labeled text input with inline error display.

**How it works:** It wraps an `<input>` in a `<label>`. `const inputId = id ?? rest.name;` ties the label to the field via `htmlFor` (accessibility). When `error` is set it swaps the border to red and shows the message below. `...rest` forwards `value`, `onChange`, `type`, etc.

**Connects to:** the auth pages (`LoginPage`, `RegisterPage`).

#### `frontend/src/shared/ui/Card.tsx`

**Purpose:** A rounded, bordered white container with an optional titled header and actions slot.

**How it works:** If either `title` or `actions` is provided it renders a header row (title on the left, `actions` node on the right); the body wraps `children` in padding. `className` lets callers extend styling.

**Connects to:** used all over the dashboards, tickets, KB, docsearch, analytics, admin, and auth pages.

#### `frontend/src/shared/ui/Spinner.tsx`

**Purpose:** A centered loading spinner with an optional label.

**How it works:** An animated CSS-border circle (`animate-spin`) plus optional text. Used as the loading placeholder in queries.

**Connects to:** shown while `query.isLoading` on most list/detail pages.

#### `frontend/src/shared/ui/Badge.tsx`

**Purpose:** A small colored pill for statuses/priorities that can auto-pick its color.

**How it works:** `TONES` maps five tone names to background/text classes. The clever part is `STATUS_TONE`, which maps many domain strings ("open", "resolved", "urgent", "published", "draft"…) to a tone. In the component, `const resolved = tone ?? STATUS_TONE[String(children)] ?? "gray";` means: if a `tone` prop is passed use it; otherwise infer a tone from the text content; otherwise fall back to gray. So `<Badge>{ticket.status}</Badge>` colors itself automatically.

**Connects to:** tickets, KB, notifications, dashboard, profile, docsearch, topbar.

#### `frontend/src/shared/ui/PageHeader.tsx`

**Purpose:** A consistent page title block with optional subtitle and right-aligned actions.

**How it works:** Renders an `<h1>` title, optional subtitle paragraph, and an `actions` slot on the right (e.g. the KB page's "New article"/"Upload" buttons).

**Connects to:** used at the top of nearly every page.

#### `frontend/src/shared/ui/EmptyState.tsx`

**Purpose:** A dashed-border placeholder for "nothing here" / "service unavailable" situations.

**How it works:** Renders a centered `title` and optional `hint`. Simple and reused.

**Connects to:** list pages use it for empty and error states.

#### `frontend/src/shared/ui/ToastContainer.tsx`

**Purpose:** Renders the stack of toast notifications and auto-dismisses them.

**How it works:** It subscribes to `toasts` and `dismissToast` from the UI store. An effect sets a 4-second `setTimeout` per toast to auto-dismiss, cleaning up timers on change. Toasts render as clickable buttons (clicking dismisses early) fixed to the bottom-right, colored by `kind`.

**Connects to:** mounted once in `App.tsx`; fed by `uiStore` (via `toast.*`).

#### `frontend/src/shared/ui/index.ts`

**Purpose:** Barrel file re-exporting all UI primitives.

**How it works:** Just `export { ... }` lines so a page can `import { Button, Card } from "@/shared/ui"`. (In practice many files still import each primitive by its full path.)

**Connects to:** all UI primitives.

#### `frontend/src/shared/ui/Markdown.tsx`

**Purpose:** A tiny, dependency-free Markdown renderer tuned for AI answers and KB articles. It deliberately avoids a big library.

**How it works:**
- `inline(text, keyBase)` handles inline formatting. It splits the text on the regex `/(\*\*[^*]+\*\*|`[^`]+`)/g`, which captures `**bold**` and `` `code` `` spans while keeping them in the array. Bold parts become `<strong>`, code parts a styled `<code>`, and everything else a `<span>`. Each node gets a unique `key`.
- `Markdown({ content })` is a small line-based block parser. It splits `content` into lines and walks them, accumulating list items in a `list` buffer with an `ordered` flag. A `flush(key)` helper emits the buffered items as an `<ol>` or `<ul>` and resets the buffer — it's called whenever a non-list line appears, so lists close correctly.
- Per line, in order, it recognizes:
  - **Headings** `/^#{1,6}\s/` → rendered as a bold `<h4>` (all heading levels collapse to one visual size).
  - **Blockquotes** `/^>\s?/` → a left-bordered callout; if the text matches `/warning/i` it's amber, `/success/i` it's emerald, otherwise slate. This is how the AI can render styled "Warning"/"Success" notes.
  - **Checkbox items** `- [ ]` / `- [x]` → a list row with a ☐ or ☑ glyph (checked ones green).
  - **Bullet items** `-`/`*` and **numbered items** `\d+.` → `<li>`s (numbered sets `ordered = true`).
  - **Blank line** → `flush` (ends any open list).
  - **Anything else** → a paragraph.
- A final `flush("md-final")` closes a list that runs to the end of the content.

**Connects to:** used by the chat `MessageList` (to render finished assistant answers) and by the KB page (article viewer and the live-preview pane in the editor).

---

### Module: auth

#### `frontend/src/modules/auth/api.ts`

**Purpose:** The four auth network calls.

**How it works:** `authApi` wraps `apiClient` calls, each returning the unwrapped `.data`: `login` → `POST /auth/login` (returns `TokenResponse`), `register` → `POST /auth/register` (returns `UserResponse`), `me` → `GET /auth/me` (the current user), and `logout` → `POST /auth/logout` with the refresh token (so the server can revoke it).

**Connects to:** the typed request/response DTOs; consumed by `useAuth`.

#### `frontend/src/modules/auth/useAuth.ts`

**Purpose:** The central auth hook — login, register, logout, and access to the current user — built on React Query mutations plus the Zustand store.

**How it works:**
- It pulls state and setters from `useAuthStore()`.
- **login** is a `useMutation` whose `mutationFn` runs a two-step flow: call `authApi.login` to get tokens, store them with `setTokens` (so the next request is authenticated), record the org slug, then call `authApi.me()` and store the user. On error it fires `toast.error(problemMessage(error))`. Returning `me` lets callers act on success.
- **register** just calls `authApi.register`; on success it toasts "Account created — please sign in." (registration doesn't auto-login).
- **logout** is a plain async function: it best-effort calls `authApi.logout(refreshToken)` inside a `try/catch` (a failure there shouldn't block logout), then `clear()`s the store and `queryClient.clear()`s all cached data so no stale info leaks to the next user.
- Returns `{ user, isAuthenticated, login, register, logout }`.

**Connects to:** `authApi`, `authStore`, `queryClient`, `problemMessage`, and `toast`. Used by `LoginPage`, `RegisterPage`, `Topbar`, `Sidebar`, dashboards, profile, and ticket detail (for role checks).

#### `frontend/src/modules/auth/LoginPage.tsx`

**Purpose:** The sign-in screen.

**How it works:** Local state for `orgSlug` (prefilled `"acme"`), `email`, `password`. `onSubmit` prevents the default form submit and calls `login.mutate({...}, { onSuccess: () => navigate("/", { replace: true }) })` — on success it goes to the dashboard. The submit button shows `loading={login.isPending}`. A footer link routes to `/register`. UI is built from `Card`, `Input`, `Button`.

**Connects to:** `useAuth`; navigates via React Router.

#### `frontend/src/modules/auth/RegisterPage.tsx`

**Purpose:** The account-creation screen.

**How it works:** It keeps the whole form in one `form` object and uses a curried `update(key)` helper to produce per-field `onChange` handlers (`setForm(prev => ({ ...prev, [key]: value }))`). `onSubmit` calls `register.mutate(form, { onSuccess: () => navigate("/login") })`. The password field enforces `minLength={8}` in the browser. Footer link returns to `/login`.

**Connects to:** `useAuth`; the shared UI primitives.

---

### Module: chat

#### `frontend/src/modules/chat/ChatPage.tsx`

**Purpose:** The AI chat experience: send a message, stream the answer token-by-token, show citations and quick replies, and support cancel.

**How it works:**
- State: `turns` (the conversation array), `streaming` (is a response in flight), `quickReplies` (suggested follow-ups). `threadId = useMemo(() => newId(), [])` creates a stable per-session thread id (`crypto.randomUUID()`). `abortRef` holds the current `AbortController`. `autoSent` guards a one-time auto-send.
- `patchAssistant(id, patch)` is a helper to immutably update one turn by id.
- **`send(text)`** is the core:
  - It appends a `user` turn and an empty `assistant` turn marked `streaming: true`, clears quick replies, and sets `streaming`.
  - It creates an `AbortController`, stores it in `abortRef`, and calls `streamChat({ message, thread_id }, { signal, onEvent, onError })`.
  - **`onEvent`** switches on `event.type`:
    - `"token"` → appends `event.data.text` to the assistant turn's `content` (this produces the live typing effect).
    - `"citations"` → patches the turn's `citations`.
    - `"quick_replies"` → sets `quickReplies` from `event.data.options`.
    - `"done"` → marks the turn `streaming: false`.
    - `"error"` → marks it done, replaces content with an apology, and toasts the error.
  - **`onError`** (network/transport failure) sets a friendly "chat service not reachable yet" message and toasts.
  - After the stream resolves it defensively re-marks the turn not-streaming, clears `streaming`, and nulls `abortRef`.
- **Auto-send from the dashboard:** an effect reads `searchParams.get("q")`; if present and not already sent, it flips `autoSent.current = true` and calls `send(q)`. This is how the dashboard's suggestion chips (which link to `/chat?q=...`) start a conversation automatically. The empty dependency array (with an eslint-disable) ensures it runs once.
- **`cancel()`** aborts the controller and clears `streaming` (the SSE helper swallows the resulting `AbortError`).
- **Render:** a `PageHeader`, then a scrollable area showing either an empty-state hint or `<MessageList turns={turns} conversationId={threadId} />`. Below that, when `quickReplies` exist and we're not streaming, it renders quick-reply pills. Clicking a pill clears the replies and, unless it's an "Other"/"Something else" option (matched by `/^(other|something else)$/i`), sends it. Finally `<Composer .../>`.

**Connects to:** `streamChat` (`shared/api/sse.ts`), `MessageList`, `Composer`, `Citation` type, `toast`, `PageHeader`. Entered from the sidebar and from dashboard suggestion links.

#### `frontend/src/modules/chat/Composer.tsx`

**Purpose:** The message input box at the bottom of the chat.

**How it works:** Controlled `textarea` with local `text` state. `submit` trims the text, ignores empty input, calls `onSend`, and clears the box. `onKeyDown` sends on Enter but inserts a newline on Shift+Enter. The action button flips between a red "Stop" (calls `onCancel`) while `streaming` and a "Send" button otherwise; "Send" is disabled when `disabled` is true (i.e., during streaming).

**Connects to:** rendered by `ChatPage`, which passes `disabled`, `streaming`, `onSend`, `onCancel`. Uses the `Button` primitive.

#### `frontend/src/modules/chat/MessageList.tsx`

**Purpose:** Renders the list of chat turns as chat bubbles, with per-state formatting, citations, and feedback.

**How it works:**
- It exports the `ChatTurn` interface (id, role, content, optional citations, `streaming`, `feedbackHandle`) — the shared shape used by `ChatPage`.
- Each turn is a flex row aligned right for `user`, left for `assistant`. The bubble styling differs (brand-colored for the user, white/bordered for the assistant).
- The content rendering has three branches:
  1. assistant + streaming + no content yet → `<ThinkingSteps />` (the animated "AI is working" checklist).
  2. assistant + not streaming → `<Markdown content={turn.content} />` (fully formatted final answer).
  3. otherwise (the user's message, or an assistant message still streaming text) → a `whitespace-pre-wrap` paragraph, plus a pulsing `▋` cursor while streaming.
- If the turn has citations, it renders a "Sources:" footer numbering each as `[n] source_uri` (falling back to the first 8 chars of `doc_id`).
- For a finished assistant turn with content, it renders `<FeedbackButtons conversationId={conversationId} />`.

**Connects to:** `ThinkingSteps`, `Markdown`, `FeedbackButtons`, and the `Citation` type. Consumed by `ChatPage`.

#### `frontend/src/modules/chat/ThinkingSteps.tsx`

**Purpose:** A faux "AI reasoning" progress indicator shown before the first token arrives, mirroring the backend agent pipeline.

**How it works:** `STEPS` lists four stages (understand → search KB → draft → verify grounding). Local `active` state starts at 0. An interval every 1100 ms advances `active` up to the last step (`Math.min(a + 1, STEPS.length - 1)`) and is cleared on unmount. Each step renders as done (✓, green), current (◐, brand, with an animated "…"), or upcoming (○, gray), giving the sense of work in progress.

**Connects to:** rendered by `MessageList` while an assistant turn is streaming with no content yet.

---

### Module: feedback

#### `frontend/src/modules/feedback/FeedbackButtons.tsx`

**Purpose:** Thumbs-up/down rating on an AI answer.

**How it works:** A `submit(rating)` async function: if there's no `conversationId` it no-ops; otherwise it `POST`s `/feedback` with `{ conversation_id, rating }` and toasts success, or toasts an error on failure. It renders two buttons ("👍 Helpful", "👎 Not helpful"). Note it uses `apiClient` directly rather than a dedicated `api.ts`.

**Connects to:** `apiClient`, `toast`. Rendered by `MessageList`.

---

### Module: dashboard

#### `frontend/src/modules/dashboard/DashboardPage.tsx`

**Purpose:** The role-aware home page. It renders one of three dashboards (admin, engineer, end user), each assembled from small reusable widgets and live queries.

**How it works:**
- **Presentational helpers:**
  - `StatCard` — a big number with a caption and optional accent color.
  - `ActionTile` — a `Link`-wrapped `Card` for quick navigation (icon + title + hint).
  - `StatusBars` — a horizontal bar chart from a `Record<string, number>`; it sorts entries descending, computes `max` (min 1 to avoid divide-by-zero), and sizes each bar `width: (v/max)*100%`. Keys are prettified (`k.replace(/_/g, " ")`) and colored via `STATUS_COLOR`.
  - `TrendBars` — a vertical bar chart of daily counts (last 7 days), each bar height scaled to `max`, labeled with the day (`d.date.slice(5)`).
- **Data hooks:** `useStats()` wraps `useQuery(["tickets","stats"], ticketsApi.stats)`. `KpiRow` renders a row of `StatCard`s from a config array (`{ key, label, accent }`) reading `stats[key]`. `RecentTickets`, `MyTicketsCard`, `RecentTicketsList`, and `NotificationsCard` each run their own query (tickets list / notifications list) and render lists with `Spinner`/`EmptyState` fallbacks. `NotificationsCard` reads `n.payload` as `{ title?, body? }` and shows an unread dot when `n.status !== "read"`.
- **AdminDashboard** shows a 4-KPI row (total/open/urgent/resolved), a "Tickets by status" `StatusBars`, a "last 7 days" `TrendBars`, an "AI activity" card that maps `analyticsApi.summary().counts` to friendly labels (`alabels`), and a two-column row of recent tickets + notifications.
- **EngineerDashboard** shows a KPI row, a "Queue by status" chart, the support queue, and notifications.
- **UserDashboard** shows four `ActionTile`s, a "Need help? Ask the AI" card whose suggestion chips navigate to `/chat?q=<encoded question>` (triggering the chat auto-send), an "Open AI Chat" link, a `MyTicketsCard`, and notifications.
- **`DashboardPage`** reads the user's role, computes a `subtitle`, and picks the right dashboard: admin → `AdminDashboard`; `support_engineer`/`sme_reviewer` → `EngineerDashboard`; everyone else → `UserDashboard`.

**Connects to:** `analyticsApi`, `ticketsApi`, `notificationsApi`, `useAuth`, `queryKeys`, and many UI primitives. Its suggestion links drive `ChatPage`.

---

### Module: tickets

#### `frontend/src/modules/tickets/api.ts`

**Purpose:** Ticket network calls plus the ticket-related types.

**How it works:** Declares `TicketMessage` (a chat line on a ticket), `TicketDetail` (extends the base `Ticket` with escalation/assignment fields), and `TicketStats` (totals plus `by_status`, `by_priority`, and a `daily` array). `ticketsApi` methods: `list(page)` → paginated `GET /tickets`; `stats()` → `GET /tickets/stats`; `get(id)`; `messages(id)`; `postMessage(id, text)`; and `suggestReply(id)` → `POST /tickets/{id}/suggest-reply` returning just `.suggestion` (an AI-drafted reply for engineers).

**Connects to:** `apiClient`, the base `Ticket`/`Page` types. Consumed by `TicketsPage`, `TicketDetailPage`, and the dashboard.

#### `frontend/src/modules/tickets/TicketsPage.tsx`

**Purpose:** The paginated ticket list.

**How it works:** `usePagination()` provides page state; a `useQuery` keyed `queryKeys.tickets("all", page)` fetches `ticketsApi.list(page)`. It renders (in order) a `Spinner` while loading, an `EmptyState` on error, an `EmptyState` when there are no items, or a table of tickets otherwise. Each row is clickable (`navigate(/tickets/${id})`) and shows subject, category, and `Badge`s for priority/status. Prev/Next buttons call `pagination.prev`/`next` (Prev disabled on page 1).

**Connects to:** `ticketsApi`, `queryKeys`, `usePagination`, UI primitives; links to `TicketDetailPage`.

#### `frontend/src/modules/tickets/TicketDetailPage.tsx`

**Purpose:** A single ticket with a live, chat-style conversation between the user and support engineer, plus an AI "suggest reply" tool for staff.

**How it works:**
- `useParams()` gives the `id`. `useAuth()` determines role; `isEngineer` checks membership in `ENGINEER_ROLES`; `mySide` is `"engineer"` or `"user"` (used to decide which bubbles are "mine"). `fmt` safely formats timestamps.
- Two queries: `ticket` (`ticketsApi.get(id)`) and `messages` (`ticketsApi.messages(id)`), the latter with **`refetchInterval: 5000`** for near-real-time polling. Both are `enabled: !!id`.
- An effect scrolls a bottom sentinel `endRef` into view whenever `messages.data` changes, keeping the newest message visible.
- **Mutations:** `send` posts a new message and, on success, clears the box and invalidates the messages query (so the new message appears immediately). `suggest` calls `suggestReply` and drops the AI draft straight into the textarea via `setText(s)`. Both toast on error.
- **Render:** loading spinner, then a not-found fallback with a back link. Otherwise a header (subject, category, priority/status badges), a conversation panel with a fake "online" indicator, and the message list. Each bubble is right-aligned and brand-colored when `m.sender_role === mySide`, with a sender label + timestamp and a "✓✓ Read" marker on my own messages.
- The composer: engineers get an "✨ AI suggest reply" button (with loading state) above the input. The textarea sends on Enter (Shift+Enter = newline). There's a disabled "Attachments coming soon" hint.

**Connects to:** `ticketsApi`, `useAuth`, `queryClient`, `problemMessage`, `toast`, UI primitives.

---

### Module: knowledge-base

#### `frontend/src/modules/knowledge-base/api.ts`

**Purpose:** KB network calls and detail types.

**How it works:** `KnowledgeDocumentDetail` extends the base doc with `retrieval_namespace` and `body`; `KbVersion` describes a version-history entry. `knowledgeApi`: `list(page, q?)` (search by title via optional `q` param), `get(id)`, `create({title, category, body})` → `POST /kb/documents/create`, `edit(id, {title?, body?})` → `PATCH`, `publish`/`unpublish`, `versions(id)`, and `upload(file, category)` which builds a `FormData` and posts multipart to `/kb/documents` (for ingesting a raw file).

**Connects to:** `apiClient`, base `KnowledgeDocument`/`Page` types. Consumed by `KnowledgeBasePage`.

#### `frontend/src/modules/knowledge-base/KnowledgeBasePage.tsx`

**Purpose:** Browse/search KB articles, and (for editors) create, edit, publish/unpublish, upload, and view version history — with a live Markdown preview.

**How it works:**
- `EDITOR_ROLES = ["sme_reviewer", "admin"]` gates authoring; `CATEGORIES` is the fixed category list; `invalidateKb()` invalidates the `["knowledge"]` cache.
- **`EditorModal`** (create/edit) keeps `title`, `category`, `body` state (body prefilled with a Markdown skeleton). A `save` mutation branches on `mode` — `create` vs `edit(initial.id, ...)`. On success it toasts, invalidates the list and the specific `["kb", id]` cache, and closes. The modal is a two-column layout: a form on the left (category is disabled when editing) and a **live `<Markdown content={body} />` preview** on the right. Clicking the dim backdrop closes it; `e.stopPropagation()` on the inner panel prevents accidental close. Save is disabled until title and body are non-empty.
- **`ArticleModal`** (viewer) fetches the article via `["kb", id]` and, for editors only (`enabled: isEditor`), its `versions`. A `setStatus` mutation publishes/unpublishes and refreshes caches. It shows the title, category, status `Badge`, version, and the rendered `Markdown` body; editors also get Edit and Publish/Unpublish buttons and a version-history list.
- **`KnowledgeBasePage`** ties it together: pagination, a search box bound to `q` (fed into the query key `["knowledge", page, q]`), an upload flow (hidden file input triggered by a button; `onFile` fires the `upload` mutation), and the list of articles (each a button that opens the viewer). Modals are conditionally rendered from `selected` (viewer) and `editor` (create/edit) state; the viewer's "Edit" hands the doc to the editor modal.

**Connects to:** `knowledgeApi`, `useAuth`, `queryClient`, `problemMessage`, `usePagination`, `toast`, `Markdown`, and UI primitives.

---

### Module: ai-data

#### `frontend/src/modules/ai-data/api.ts`

**Purpose:** The single "ask the database in English" call.

**How it works:** `AiDataResult` describes the response: the natural-language `answer`, the `tool` the LLM chose, its `args`, which `planner` decided (`"llm"` or `"keyword-fallback"`), and the raw `result`. `aiDataApi.query(instruction)` posts `{ instruction }` to `/ai/query`.

**Connects to:** `apiClient`; consumed by `AiDataPage`.

#### `frontend/src/modules/ai-data/AiDataPage.tsx`

**Purpose:** A natural-language interface to database operations, showing which tool the LLM picked and the raw result.

**How it works:** Local `instruction` state and a `run` mutation calling `aiDataApi.query`. `submit(text)` trims, sets the instruction, and runs the mutation; a set of `EXAMPLES` chips call `submit` directly. The textarea sends on Enter (Shift+Enter = newline). While pending it shows a "LLM is choosing…" line. When `run.data` arrives it renders a card with the chosen `tool` (as a `Badge`), the `planner`, the natural-language `answer`, and a collapsible `<details>` with the pretty-printed raw `result` JSON.

**Connects to:** `aiDataApi`, `problemMessage`, `toast`, UI primitives.

---

### Module: docsearch

#### `frontend/src/modules/docsearch/api.ts`

**Purpose:** Upload/inspect/search calls for ad-hoc document search, plus its types.

**How it works:** Types: `InspectResult` (filename, source type, optional sheet names for Excel), `UploadedDoc` (an indexed source with `chunk_count`), `DocHit` (a search result with `text`, `score`, `summary`, and `location`), and `SearchResult`. `docsearchApi`: `inspect(file)` (multipart — peeks at a file, e.g. to discover Excel tabs), `upload(file, sheet?)` (multipart index), `addUrl(url)` (index a web page), `list()` (returns `.documents`), `remove(id)` (DELETE), and `search(query)` (POST).

**Connects to:** `apiClient`; consumed by `DocumentSearchPage`.

#### `frontend/src/modules/docsearch/DocumentSearchPage.tsx`

**Purpose:** Attach files or URLs, then keyword-search across them, with an Excel tab picker and a source-viewer modal.

**How it works:**
- `refreshDocs()` invalidates the `["docsearch","docs"]` list. `HitModal` shows a hit's exact source text in a `<pre>`.
- State: `pending` (an Excel file awaiting a tab choice), `chosenSheet`, `url`, `query`, and `selected` (the hit shown in the modal). `docs` query loads the attached-source list.
- **Upload flow with inspect-then-upload:** `onFile` resets the input and fires `inspectMut`. In `inspectMut.onSuccess`, if the file has more than one sheet it stashes `pending` and defaults `chosenSheet` to the first tab (prompting the user); otherwise it uploads immediately with the single sheet. `uploadMut` toasts the indexed passage count and refreshes. The amber "which tab?" panel lets the user pick a sheet and index it.
- `urlMut` adds a URL; `removeMut` deletes a source; `searchMut` runs the search (no auto-refresh — it's a mutation whose `data` holds the last results).
- **Render:** an "Attach" card (file button + URL input + optional tab picker + the attached-sources list with remove buttons), then a "Search" card (search input, Enter to search) that lists hits as clickable cards showing the `summary`, source type badge, filename, and location; clicking opens `HitModal`. Empty results show an `EmptyState`.

**Connects to:** `docsearchApi`, `queryClient`, `problemMessage`, `toast`, UI primitives.

---

### Module: notifications

#### `frontend/src/modules/notifications/api.ts`

**Purpose:** Notification network calls.

**How it works:** `notificationsApi`: `list(page)` (paginated), `markRead(id)`, `markAllRead()`, and `unreadCount()` which returns just `.count`. 

**Connects to:** `apiClient`, `NotificationDTO`/`Page` types. Consumed by `NotificationsPage`, `Topbar` (the bell), and the dashboard.

#### `frontend/src/modules/notifications/NotificationsPage.tsx`

**Purpose:** The full notification list with per-item "Mark read".

**How it works:** `usePagination` + a `useQuery` keyed `queryKeys.notifications(page)`. A `markRead` mutation invalidates that same key on success so the item updates in place. Renders `Spinner`/error `EmptyState`/empty `EmptyState`/list. Each row shows the notification `type`, a localized timestamp, a status `Badge`, and (when not already read) a "Mark read" button.

**Connects to:** `notificationsApi`, `queryClient`, `queryKeys`, `usePagination`, UI primitives.

---

### Module: profile

#### `frontend/src/modules/profile/ProfilePage.tsx`

**Purpose:** A read-only view of the signed-in user's account.

**How it works:** Reads `user` from `useAuth`. Derives `initials` from the first two email characters and a friendly `roleLabel` from `ROLE_LABEL`. Renders an avatar circle with initials, the email, and a definition list of Email, Role (as a `Badge`), Organization (hardcoded "Acme Corp (acme)"), and Status ("Active"). There's no editing here — it's purely informational.

**Connects to:** `useAuth`, `Badge`.

---

### Module: analytics

#### `frontend/src/modules/analytics/api.ts`

**Purpose:** The analytics summary call.

**How it works:** `AnalyticsSummary` is `{ counts: Record<string, number> }`; `analyticsApi.summary()` GETs `/analytics/summary`.

**Connects to:** `apiClient`; consumed by `AnalyticsPage` and the admin dashboard.

#### `frontend/src/modules/analytics/AnalyticsPage.tsx`

**Purpose:** An admin-only metrics grid (deflection, escalation, KB activity).

**How it works:** A `useQuery` keyed `queryKeys.analytics` calls `analyticsApi.summary`; `counts` defaults to `{}`. It shows `Spinner`/error `EmptyState`/empty `EmptyState`, or a responsive grid of `Card`s — one per count — rendering the number and a friendly label from `LABELS` (falling back to the raw key).

**Connects to:** `analyticsApi`, `queryKeys`, UI primitives. Reached only via the `["admin"]` `RoleRoute`.

---

### Module: admin

#### `frontend/src/modules/admin/AdminPage.tsx`

**Purpose:** An admin reference screen showing the RBAC roles and the category-to-queue/SLA routing registry.

**How it works:** It's entirely static (no queries). `CATEGORIES` is a hardcoded list mirroring the backend's seed `category_registry` (each `{ key, queue, sla }`), and `ROLES` lists the four roles. It renders two `Card`s side by side: a bulleted list of roles, and a table of category → queue → SLA. This is a read-only window into the platform's routing configuration.

**Connects to:** `Card`, `PageHeader`. Reached only via the `["admin"]` `RoleRoute`.

---

### How it all fits together (data + control flow)

- **Startup:** `main.tsx` applies the theme, then renders `App`, which mounts the router and the toast layer.
- **Auth gate:** unauthenticated users hitting any app route are bounced to `/login` by `ProtectedRoute`. `useAuth.login` stores tokens in the persisted `authStore`, then loads the user.
- **Every request** flows through `apiClient`, which injects the JWT and, on a 401, silently refreshes the token once (single-flight) and replays the request — or logs the user out if refresh fails.
- **Server data** is fetched/cached with React Query using keys from `queryKeys`; mutations invalidate those keys to refresh the UI. Polling powers the bell's unread count (20s) and the ticket conversation (5s).
- **The chat** is special: instead of React Query it streams via `sse.ts`, updating local `turns` state frame-by-frame, and uses `Markdown` for the final answer, `ThinkingSteps` for the "working" state, and `FeedbackButtons` for ratings.
- **Role shapes the UI** in three places that agree with each other: the `Sidebar` (which links show), the `routes.tsx` guards (which pages are reachable), and the `DashboardPage` (which dashboard renders).
- **Toasts** (`uiStore` + `ToastContainer`) and the **UI primitives** (`shared/ui`) give consistent feedback and styling across every module.

---

## Scripts, Infra & Runbook

This section explains everything you need to **run**, **seed**, and **verify** the Enterprise AI Helpdesk — from the one-command launcher, through the seeder scripts that fill the database, down to the Docker files that package the whole stack. Read it top to bottom and you will understand exactly what happens between typing `python start.py` and seeing a working app in the browser.

Before diving into files, here is the mental model of how the pieces fit together:

- **`start.py`** is the front door for *local* development. It finds a database, creates a Python virtual environment, seeds demo data, and launches the backend + frontend as background processes.
- **`docker-compose.yml` + the Dockerfiles + `entrypoint.sh`** are the front door for *containerized* runs. Instead of background processes, everything runs in 7 containers.
- **The seed scripts** (`seed_demo.py`, `demo_kb_data.py`, `bootstrap_admin.py`) fill PostgreSQL with roles, users, categories, and knowledge-base articles so the AI has something to answer from.
- **`verify.py`** is the "is it actually working?" checker, and **`db_browser.py`** lets you peek inside the database from the terminal.
- **`.env.example`** is the settings template that every one of these reads from.

---

### `start.py` — the one-command launcher

**Purpose:** A single, pure-standard-library Python script that starts the entire local stack (database → backend → frontend) with `python start.py`, and tears it down with `python start.py --stop`. It deliberately uses **no PowerShell and no third-party libraries** so it can run with a bare `python` before any dependencies are installed.

**How it works:**

The module docstring (lines 1-21) is also the usage guide. It documents the four modes and — importantly — the *decision logic* the script uses to find a database and a Python interpreter:

```
Database   : an already-running Postgres on :5432  ->  reuse it
             else `--docker`/Docker present         ->  docker compose up db
             else a bundled Postgres in %LOCALAPPDATA%\helpdesk -> start it
Backend py : backend/.venv  ->  %LOCALAPPDATA%\helpdesk\venv  ->  create backend/.venv
```

It also states a key design principle: **Redis and ChromaDB are optional locally** — the app "degrades gracefully" (rate-limiting fails open, dense vector search is skipped, and sparse full-text search still retrieves).

*Path and constant setup (lines 35-59):* `ROOT`, `BACKEND`, `FRONTEND`, and `LOGS` are computed from the file's own location, so the script works no matter where you invoke it from. It hard-codes `BACKEND_PORT = 8000` and `FRONTEND_PORT = 5280`. The `STACK` path points at a "bundled durable stack" under `%LOCALAPPDATA%\helpdesk` — a portable PostgreSQL and venv that a separate setup step installs for machines without Docker. The `DEMO_USERS` list and `DEMO_PASSWORD = "ChangeMe123!"` are printed at the end so you know how to log in.

*Windows detachment (line 51):*
```python
DETACHED = 0x00000008 | 0x00000200 if IS_WIN else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
```
These Windows flags let child processes (uvicorn, npm) keep running **after `start.py` exits**, instead of dying with the parent.

*Small helpers (lines 65-167):*
- `say`/`ok`/`warn`/`die` print tidy status lines like `[ OK ] backend healthy`; `die` also exits with code 1.
- `load_env` is a tiny hand-rolled `.env` parser. It reads `key=value` lines, skips comments and blanks, and strips inline `# comments` plus surrounding quotes. (This same parser is copy-pasted into `verify.py` and `db_browser.py` — none of them depend on `python-dotenv`.)
- `port_open` / `wait_for_port` do a TCP connect to check if a service is listening, polling every 0.5s up to a timeout. This is how the script "waits for the database."
- `http_get` / `wait_for_http` poll an HTTP URL until it returns 200 — used to wait for the backend's `/health` endpoint.
- `netstat_pids` and `kill_port` are the "stop" machinery: they parse `netstat -ano` output to find which process IDs are LISTENING on a port, then run `taskkill /F /T` to kill them. This is how `--stop` frees ports 8000 and 5280 without PowerShell.
- `launch` is the core process spawner:
  ```python
  subprocess.Popen(cmd, cwd=str(cwd), stdout=logf, stderr=subprocess.STDOUT,
      env={**os.environ, **(env or {})},
      creationflags=DETACHED if IS_WIN else 0, close_fds=True)
  ```
  It redirects both stdout and stderr into a log file under `logs/`, merges any extra env vars on top of the current environment, and detaches the child on Windows.

*Resolution functions (lines 173-225):*
- `backend_python()` implements the "which Python?" fallback chain: prefer `backend/.venv`, then the durable venv, and if neither exists it **creates `backend/.venv` and pip-installs `requirements.txt`** (a one-time bootstrap).
- `ensure_database()` implements the database fallback chain. First it checks `port_open(host, port)` — if Postgres is already up, it reuses it. Otherwise, if `--docker` was passed (or Docker is installed and no bundled Postgres exists) it runs `docker compose up -d postgres redis chromadb`. Otherwise it launches the bundled `postgres.exe`. If none of those work, it `die()`s with a clear message. Finally it waits up to 30s for the port.
- `seed()` shells out to the seeder:
  ```python
  subprocess.run([py, "scripts/seed_demo.py"], cwd=str(BACKEND),
      env={**os.environ, "PYTHONPATH": str(BACKEND)})
  ```
  Note it sets `PYTHONPATH=backend` so the seeder can `import app...`. If seeding fails, the whole launch aborts.

*Commands (lines 231-303):*
- `do_stop()` kills the two ports and, if a bundled Postgres data dir exists, runs `pg_ctl ... stop -m fast`.
- `do_start()` is the main flow: sanity-check that `backend/` exists and Python is on PATH → parse `backend/.env` → resolve the venv → `ensure_database` → `seed`. If `--seed-only`, it stops here. Otherwise it kills any stale backend, launches uvicorn (`app.main:app` on 127.0.0.1:8000), and waits up to 45s for `/health`. Then, unless `--no-frontend`, it launches the Vite dev server via `npm run dev -- --port 5280 --strictPort --host` (wrapping `npm` in `cmd /c` on Windows). Finally it prints the big **READY** banner with the UI/API/docs/health URLs and all four demo logins.

*`main()` (lines 306-316):* Standard `argparse` with four flags (`--stop`, `--seed-only`, `--no-frontend`, `--docker`).

**Connects to:**
- Reads `backend/.env` (the copy of `.env.example`) for Postgres host/port.
- Calls `backend/scripts/seed_demo.py` to populate the DB.
- Launches `app.main:app` (the FastAPI backend) and the Vite frontend.
- Writes logs to `logs/backend.log`, `logs/frontend.log`, `logs/postgres.log`.
- Is the CLI equivalent of `start.py` (PowerShell) and of `docker compose up` (containers).
- Points users at `scripts/verify.py` at the end for the health check.

---

### `scripts/verify.py` — end-to-end health check

**Purpose:** Runs after the stack is up and prints **PASS / FAIL / SKIP** for every component (backend, DB, Redis, Chroma, frontend, login, chat, Gemini, tickets, retrieval, unit tests). It exits non-zero only if a *critical* component fails, so it works as a CI gate. Optional components never fail the overall result.

**How it works:**

*Config (lines 26-38):* Hard-codes the same ports as `start.py` and defines the verification identity — org `acme`, `user@acme.com`, password `ChangeMe123!` (these must exist, which is why you seed first). It sets up ANSI color codes and a `results` list of `(name, status, detail, critical)` tuples.

*Helpers:*
- `enable_ansi()` (lines 41-49) turns on ANSI color escape handling on Windows via a `ctypes` call to `SetConsoleMode`.
- `record()` (lines 52-56) appends a result and prints a colored `[ PASS ]`/`[ FAIL ]`/`[ SKIP ]` line; critical rows get blank padding, optional rows get an ` opt ` tag.
- `load_env()` is the same `.env` parser as `start.py`.
- `http()` is a minimal urllib-based HTTP client returning `(status_code, body_bytes)`, catching `HTTPError` (returns the real code) and any other exception (returns code `0`).
- `chat()` (lines 95-120) is the interesting one — it drives **one full SSE (Server-Sent Events) chat turn**. It POSTs to `/api/v1/chat/messages` with a Bearer token, then iterates line-by-line over the streamed response, tracking the current `event:` name and parsing each `data:` JSON payload. It collects `citations`, `decision`, and the final `response_text` from the `done` event, returning `{decision, citations, text}`.
- `venv_python()` finds a Python interpreter (same fallback chain as the launcher) to run pytest.

*`main()` flow (lines 136-237)* runs the checks in order, each calling `record()`:
1. **Backend + health** — `GET /health` must be 200; if not, it short-circuits and summarizes.
2. **Readiness** — `GET /health/ready` returns a `checks` dict; it records `database` (critical), `redis` and `chroma` (both `critical=False`, with messages explaining graceful degradation).
3. **Frontend** — a TCP port check on 5280 (optional).
4. **Login** — `POST /auth/login`, extracting `access_token`.
5. **Chat + retrieval + live Gemini** — sends the canonical VPN question and inspects the result:
   ```python
   chat_ok = bool(res["text"])
   retrieval_ok = len(res["citations"]) >= 1 and res["decision"] == "deliver"
   gemini_live = chat_ok and res["decision"] in ("deliver", "clarify", "escalate")
   ```
   So "retrieval works" means the answer was grounded (≥1 citation) *and* the AI chose to `deliver`.
6. **Gemini** — SKIP if `LLM_PROVIDER=fake`, otherwise PASS/FAIL based on `gemini_live`.
7. **Tickets** — `GET /tickets` returns 200.
8. **Retrieval** — reuses the `retrieval_ok` flag from step 5.
9. **Unit tests** — if a venv with pytest exists, it runs `pytest -q --tb=line` in `backend/` with `PYTHONPATH` set, then scrapes the last line mentioning "passed/failed/error" for the summary. Missing venv/pytest is a SKIP, not a FAIL.

*`summarize()` (lines 240-250)* tallies passed/failed/skipped, and if any **critical** row FAILed prints `OVERALL: FAIL` and `sys.exit(1)`; otherwise `OVERALL: PASS` and `sys.exit(0)`.

**Connects to:**
- Reads `backend/.env` for `API_V1_PREFIX` and `LLM_PROVIDER`.
- Hits the running backend's HTTP API (health, auth, chat, tickets).
- Depends on the demo data seeded by `seed_demo.py` (the login user and KB must exist).
- Runs the `backend/tests/` suite via pytest.
- Documented in the README's "VERIFICATION" section; recommended by `start.py`'s final banner.

---

### `scripts/db_browser.py` — terminal PostgreSQL browser

**Purpose:** A no-GUI database explorer. List tables with row counts, dump a table's rows, or search a table for a term — all from the terminal. Handy when you don't want to install pgAdmin/DBeaver.

**How it works:**

*Dependency guard (lines 20-24):* It tries to `import asyncpg` (the async Postgres driver, already a backend dependency) and exits with a friendly message if missing.

*`SEARCH_HINTS` (lines 30-38):* A dict mapping well-known tables to the columns worth searching (e.g. `tickets` → subject/category/status/escalation_reason). For unknown tables, `search()` falls back to scanning every text/varchar column.

*`connect()` (lines 52-60):* Reads `backend/.env` via the same `load_env` and opens an asyncpg connection using `POSTGRES_*` values (defaulting to `postgres/postgres@localhost:5432/helpdesk`).

*Query helpers:*
- `_tables()` lists public tables from `pg_tables`.
- `list_tables()` prints each table with `SELECT count(*)`, guarding each count in a try/except (prints `-1` if a table can't be counted) and totalling rows.
- `_pp()` is the pretty-printer: for each row it prints `#N` and every `column: value`, replacing newlines with spaces and truncating long values to 100 chars with an ellipsis.
- `show()` (lines 101-110) validates the table name, checks `information_schema.columns` for a `created_at` column, and if present orders by it `DESC` — so you see the newest rows first — then LIMITs.
- `search()` (lines 113-130) builds an `ILIKE` (case-insensitive) `WHERE` clause across the hinted columns:
  ```python
  where = " OR ".join(f'"{c}"::text ILIKE $1' for c in cols)
  q = f'SELECT * FROM "{table}" WHERE {where} LIMIT {limit}'
  ```
  Note the `$1` parameter binding for the `%term%` value — the *search term* is safely parameterized (SQL-injection-safe), while table/column names come from a controlled allowlist or `information_schema`.

*`interactive()` (lines 133-154):* A REPL. It lists tables once, then loops on `input("db> ")` accepting `tables`, `show <table>`, `search <table> <term>`, and `quit`.

*`main()` (lines 157-179):* Argparse supports `--tables`, `--show TABLE`, `--search TABLE TERM`, and `--limit` (default 20). It connects (with a helpful "Is the database running?" error), dispatches to the right function, and always closes the connection in a `finally`.

**Connects to:**
- Reads `backend/.env` for connection settings (same `POSTGRES_*` as everything else).
- Uses `asyncpg`, a backend dependency.
- Queries the tables created by the seeders (`users`, `tickets`, `kb_chunks`, etc.).
- Documented in the README's "DATABASE VIEWER" section.

---

### `backend/scripts/seed_demo.py` — full enterprise demo seeder

**Purpose:** The seeder `start.py` actually runs. A superset of `seed_demo.py` that loads a realistic, demo-ready dataset: schema + roles + org + one user per role, **all 31 registry categories** (8 canonical + 28 extended, per the docstring "8 canonical + 28 extended"), **93 KB articles** (from `demo_kb_data.py`), demo tickets with user↔engineer message threads, notifications, analytics events, and AI chat history. Idempotent throughout.

**How it works:**

*`_article_body()` (lines 42-61):* Turns one article dict into a single rich Markdown chunk. It assembles a heading, a metadata line (category / est. resolution time / required permissions), then `## Problem`, `## Symptoms`, `## Root Cause`, `## Step-by-step Guided Solution`, `## Related Articles`, a screenshot placeholder, and Tags/Keywords footer. The whole article becomes **one chunk** — retrieval matches the full article rather than fragments.

*`ensure_database()`* is identical to `seed_demo.py`'s.

*`main()` (lines 74-329)* — again imports inside the function, then:
- Imports `KB_ARTICLES` from `demo_kb_data` (works because the seeder is run with `cwd=backend/scripts`'s parent and `PYTHONPATH=backend`; note the `import demo_kb_data` is a sibling import).
- `Base.metadata.create_all` builds the schema.
- **Roles** — insert-if-missing (same guard pattern).
- **Categories** — instead of a hard-coded list, it reads the *in-memory* `get_category_registry()` and inserts every key not already in the DB, copying `required_intake_fields`, `thresholds`, `sla_tier`, and `handoff_queue`. This is why the demo has all 31 categories — the source of truth is the code registry.
- **Org + users** — create "acme" and the four users if missing; then build lookup dicts `users`, and pull out `admin`, `engineer`, `end_user`.
- **KB articles (93)** — for each article not already present (dedup by title), compose the body, create a `KbDocument` (PUBLISHED, SHA-256 checksum, `source_uri=seed://category/title`) and a single `KbChunk`. Counts added into `added_kb`.
- **Demo tickets** — guarded by a "marker" query (does the VPN-800 ticket already exist?). If not, it inserts 8 tickets across categories/priorities/statuses. For each it creates a `Conversation` (status `AWAITING_HUMAN`), a `Ticket` (with `escalation_reason="ai_unresolved"` and `final_confidence=0.42` — i.e. the AI gave up), a `CREATED` event, an optional `ASSIGNED` event, and one `COMMENTED` `TicketEvent` per line of the user↔engineer `thread`. It back-dates `created_at`/event timestamps so the history looks realistic.
- **Notifications** — marker-guarded; inserts 8 in-app notifications (mix of read/unread) for the demo users, back-dated.
- **Analytics events** — marker-guarded (`properties["seed"] == "demo"`); inserts a spread of event types (22 `answer_delivered`, 30 `chat_started`, etc.) across categories so the admin dashboard shows real numbers.
- **AI chat history** — if no `AI:%`-titled conversation exists, it inserts one resolved password-reset conversation with a user message and a fully formatted assistant reply carrying a `Decision.DELIVER` and a citation.
- `commit()`, `dispose()`, and a summary printing counts.

**Connects to:**
- **Imports `KB_ARTICLES` from `demo_kb_data.py`** (the 93-article data file).
- Reads the authoritative category list from `app.registries.category_registry`.
- Uses the same ORM models / session / `hash_password` as `seed_demo.py`, plus ticket/notification/analytics/conversation models.
- Invoked by `start.py`'s `seed()` and by the README's manual "Option B" steps.
- Its output (users, KB, tickets) is exactly what `verify.py` checks and what the frontend displays.

---

### `backend/scripts/demo_kb_data.py` — generated KB article dataset

**Purpose:** A large (≈226 KB), auto-generated Python module holding `KB_ARTICLES`, a list of **93** realistic knowledge-base article dicts. It is pure data — no logic — consumed by `seed_demo.py`. The header warns "do not hand-edit."

**How it works:** It defines one module-level list:
```python
KB_ARTICLES = [
 { "category_key": "password_reset",
   "title": "Reset an expired Active Directory password",
   "problem": "...", "symptoms": [...], "root_cause": "...",
   "steps": [...], "est_resolution_time": "5-10 minutes",
   "required_permissions": [...], "related_articles": [...],
   "tags": [...], "confidence_keywords": [...],
   "screenshot_placeholder": "[Screenshot: ...]" },
 ...
]
```
Each dict is a fully structured article: a problem statement, a bullet list of symptoms, a root cause, numbered resolution steps, metadata, tags, and — notably — `confidence_keywords` (phrases like `"password expired"`, `"0xc0000071"`). These keywords are the kind of high-signal terms the retrieval layer's sparse full-text search matches against. The articles span 31 categories (Password Reset, MFA, VPN, Outlook, Teams, Printer, WiFi, SAP, Oracle, AD, Docker, Python, Git, and more).

**Connects to:**
- Imported by `seed_demo.py` (`from demo_kb_data import KB_ARTICLES`), where `_article_body()` flattens each dict into a Markdown chunk.
- The `category_key` on each article must match a category in the registry (so `retrieval_namespace` lines up).
- This is the content the AI cites when it answers — e.g. the VPN-800 article that `verify.py` and the README's demo prompt rely on.

---

### `backend/scripts/check_providers.py` — live provider smoke test

**Purpose:** A quick manual check that the configured LLM + embedding provider actually work end-to-end: a completion, a streamed completion, and an embedding — through the provider abstraction. Requires the relevant API key in the environment.

**How it works:** The docstring shows how to switch providers purely via env vars (`LLM_PROVIDER`/`EMBEDDING_PROVIDER` + the matching key). `main()`:
- `get_settings()` and prints which providers are configured.
- `get_llm_provider("large")` → `await llm.generate([ChatMessage(role="user", content="Say hello ...")])`, printing the model, total tokens, and text.
- Streams a second prompt token-by-token via `async for token in llm.stream(...)`.
- `get_embedding_provider().embed([...])` and prints the vector dimension (e.g. 768).
- Prints `get_token_accountant().total` — the running token/cost tally.

**Connects to:**
- Uses the provider abstraction in `app/providers/` (`base.ChatMessage`, `registry.get_llm_provider/get_embedding_provider/get_token_accountant`).
- Reads provider selection + keys from settings (`backend/.env` or env vars).
- The README's "AI → How to verify it works" section points here; complements `verify.py`, which tests the provider *through* a live chat turn instead.

---

### `backend/scripts/bootstrap_admin.py` — container demo bootstrap

**Purpose:** The minimal seed used **inside Docker**. After Alembic migrations (which seed roles + categories), it creates one org + one admin user, gated by `BOOTSTRAP_DEMO` so it never runs in real production. All values come from env with dev defaults.

**How it works:** It inserts `backend/` onto `sys.path` so `import app...` works when run as a plain script. `_run()` reads `BOOTSTRAP_ORG_SLUG/NAME` and `BOOTSTRAP_ADMIN_EMAIL/PASSWORD` (defaults: `acme` / `admin@acme.com` / `ChangeMe123!`). In a session it: creates the org if missing; fetches the `admin` role and **errors out if roles aren't seeded** (a reminder that `alembic upgrade head` must run first); creates the admin user (password hashed) if missing; commits; and prints the sign-in line. Every step is insert-if-missing, so it's safe to re-run on every container start.

**Connects to:**
- Called by `entrypoint.sh` in the `api` role when `BOOTSTRAP_DEMO=true` (which `docker-compose.yml` sets).
- Depends on Alembic migrations having seeded roles/categories (unlike `seed_demo.py`, which builds the schema itself with `create_all`). This is the key difference: Docker uses **migrations + bootstrap_admin**, while local `start.py` uses **seed_demo** (create_all + full demo data).

---

### `docker-compose.yml` — the 7-service stack

**Purpose:** Defines the full containerized topology so `docker compose up -d --build` brings up the entire platform. The single entry point is **http://localhost** (the frontend container proxies `/api` to the backend).

**How it works:** `name: helpdesk` names the project. Seven services:

1. **`postgres`** — `postgres:16-alpine`. User/pass/db default to `helpdesk` (overridable via env). Persists to the `postgres_data` volume, exposes 5432, and has a `pg_isready` healthcheck (10s interval, 10 retries).
2. **`redis`** — `redis:7-alpine` started with `--appendonly yes` (durable). Persists to `redis_data`, exposes 6379, healthcheck `redis-cli ping`.
3. **`chromadb`** — `chromadb/chroma:0.5.5`, `IS_PERSISTENT=TRUE`, telemetry off. Persists to `chroma_data`. **Port mapping `"8001:8000"`** — Chroma listens on 8000 inside the container but is published on **8001** on the host (to avoid clashing with the API's 8000). Healthcheck curls `/api/v1/heartbeat`.
4. **`api`** — built from `./backend/Dockerfile`, `command: ["api"]` (the entrypoint role). It loads `./backend/.env` via `env_file`, then **overrides the hostnames** so containers find each other on the compose network: `POSTGRES_HOST: postgres`, `REDIS_URL: redis://redis:6379/0`, `CHROMA_HOST: chromadb`, plus Celery broker/result URLs on Redis DBs 1 and 2. It sets the demo bootstrap vars (`BOOTSTRAP_DEMO: "true"`, org/admin creds) and — importantly — forces **`LLM_PROVIDER: fake` and `EMBEDDING_PROVIDER: fake`** so the demo chat works with **no API keys** (comments explain how to switch to a real provider). It `depends_on` postgres/redis/chromadb being **healthy**, publishes 8000, and has its own `/health` healthcheck with a 40s `start_period`.
5. **`worker`** — same image, `command: ["worker"]` → a Celery worker for learning/ingestion/notification/analytics tasks. Same env wiring, depends on the three infra services being healthy.
6. **`beat`** — same image, `command: ["beat"]` → the Celery beat scheduler (periodic jobs). Only depends on redis being healthy.
7. **`frontend`** — built from `./frontend/Dockerfile` (nginx serving the React build). Publishes **`"80:80"`** and depends on `api` being started. Its nginx config proxies `/api` to `api:8000`.

The named `volumes` (`postgres_data`, `redis_data`, `chroma_data`) persist data across `docker compose down` (only `down -v` wipes them).

**Connects to:**
- Builds `backend/Dockerfile` (used by api/worker/beat — three roles from one image) and `frontend/Dockerfile`.
- Every service's `command` string maps to a `case` branch in `entrypoint.sh`.
- Reads `backend/.env` (copied from `.env.example`) and layers container-specific overrides on top.
- The `frontend` service relies on `frontend/nginx.conf` to reach `api:8000`.
- The parallel path to `start.py`: same components, but containers instead of background processes, and Alembic migrations instead of `create_all`.

---

### `backend/Dockerfile` — backend image (api / worker / beat)

**Purpose:** Builds one production image used by all three backend roles (api, worker, beat). Slim, non-root, with a container healthcheck.

**How it works:**
- Base `python:3.12-slim`, with env vars for unbuffered output, no `.pyc` files, and no pip cache — standard slim-image hygiene.
- Installs only `curl` (needed by the healthcheck) with apt lists cleaned up afterward; no compiler toolchain (dependencies install from wheels).
- **Layer-caching trick:** copies `pyproject.toml README.md alembic.ini` and the `app` package, then `pip install .` — so dependencies only reinstall when those files change, not on every code edit.
- Copies `entrypoint.sh` and `scripts/`, makes the entrypoint executable, creates a non-root `appuser` (uid 1000), chowns `/app` to it, and switches to `USER appuser` (security best practice — the container doesn't run as root).
- `EXPOSE 8000`, a `HEALTHCHECK` that curls `/health`, `ENTRYPOINT ["/entrypoint.sh"]` and default `CMD ["api"]`.

**Connects to:**
- The `ENTRYPOINT` is `entrypoint.sh`; the `CMD`/compose `command` picks the role.
- Built by the `api`, `worker`, and `beat` services in `docker-compose.yml`.
- Copies in the `scripts/` folder so `bootstrap_admin.py` is available at runtime.
- `.dockerignore` keeps `.venv`, `.env`, `tests/`, caches, and data dirs out of the build context.

---

### `backend/entrypoint.sh` — container startup orchestrator

**Purpose:** The first thing that runs in every backend container. It waits for infrastructure to be ready, then starts the requested role (`api`, `worker`, `beat`, or `migrate`).

**How it works:**
- `set -euo pipefail` — fail fast on any error or unset variable. `ROLE="${1:-api}"` defaults to `api`.
- **`wait_for_services()`** runs an inline Python heredoc that: reads settings, then loops up to 60× (2s apart) trying `psycopg.connect(...)` + `SELECT 1` on Postgres, and up to 30× pinging Redis. It prints progress and `sys.exit`s if a dependency never comes up. This is the container-native version of `start.py`'s `wait_for_port`.
- **`case "${ROLE}"`** dispatch:
  - `api` → wait, then **`alembic upgrade head`** (run migrations), then — only if `BOOTSTRAP_DEMO=true` — run `bootstrap_admin.py` (non-fatal on failure), then `exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'` (the proxy flags matter because nginx sits in front).
  - `worker` → wait, then `exec celery ... worker --concurrency=${CELERY_CONCURRENCY:-4}`.
  - `beat` → wait, then `exec celery ... beat`.
  - `migrate` → wait, then `alembic upgrade head` only (one-shot).
  - anything else → print an error and exit 64.
- Using `exec` replaces the shell with the real process so signals (like `docker stop`) reach it cleanly.

**Connects to:**
- Invoked by the Dockerfile's `ENTRYPOINT`; the role comes from each compose service's `command`.
- Runs Alembic migrations (`app/db` migrations) — this is the **containerized** schema path, in contrast to the seeders' `create_all`.
- Runs `bootstrap_admin.py` gated by the `BOOTSTRAP_DEMO` env var set in `docker-compose.yml`.
- Starts the Celery app at `app.workers.queue.celery_app`.

---

### `frontend/Dockerfile` — build the SPA, serve with nginx

**Purpose:** A two-stage build: compile the React/Vite app with Node, then serve the static bundle with a tiny nginx image that also reverse-proxies `/api` to the backend.

**How it works:**
- **Stage 1 (`build`)** — `node:20-alpine`. Copies `package.json`/`package-lock.json` first, runs `npm install --no-audit --no-fund` (cache-friendly), then copies the rest and `npm run build`, producing `/app/dist`.
- **Stage 2 (runtime)** — `nginx:1.27-alpine`. Copies `nginx.conf` to `/etc/nginx/conf.d/default.conf` and the built `dist/` from stage 1 into nginx's web root. `EXPOSE 80` and a `HEALTHCHECK` that `wget`s `/`. Only the static files ship in the final image — Node and `node_modules` are left behind in the discarded build stage, keeping it small.

**Connects to:**
- Built by the `frontend` service in `docker-compose.yml` (published on port 80).
- Bakes in `frontend/nginx.conf`, which proxies `/api/` (and `/api/v1/chat` with buffering off for SSE) to `api:8000`. This is why, under Docker, you open **http://localhost** and the same-origin `/api` calls reach the backend.
- `frontend/vite.config.ts` provides the *dev-mode* equivalent (a Vite proxy from `/api` to `http://localhost:8000`), used by `start.py`/`start.py`.
- `.dockerignore` excludes `node_modules`, `dist`, and `.env`.

---

### `backend/.env.example` — configuration template

**Purpose:** The 12-factor settings template. You copy it to `backend/.env` and fill in secrets; `app/core/config.py` (Pydantic Settings) loads it at startup. Every script in this section reads from the resulting `.env`.

**How it works:** It is grouped into labelled sections:
- **Application** — `APP_ENV` (local/dev/staging/production), `DEBUG`, `API_V1_PREFIX=/api/v1` (the prefix `verify.py` reads), `VERSION`.
- **Security / JWT** — `SECRET_KEY` (must be a strong random ≥32 chars in prod), algorithm, token lifetimes, issuer/audience.
- **PostgreSQL** — host/port/user/pass/db plus connection-pool tuning. The header comments note that Docker overrides the host values per service, so you keep `localhost` here for non-Docker runs.
- **Redis / Celery / ChromaDB** — the cache URL, Celery broker + result-backend URLs (Redis DBs 1 and 2), and Chroma host/port/collection names.
- **Provider selection** — `LLM_PROVIDER` (`gemini | openai | claude | fake`) and `EMBEDDING_PROVIDER` (`gemini | openai | fake`). This one switch is how you change AI backends.
- **Per-provider blocks** — Gemini (key + small/large/embedding models + `EMBEDDING_DIM=768`), OpenAI, and Anthropic model names.
- **Generation + resilience** — temperature, max tokens, timeout, retries, backoff, rate limit.
- **Retrieval + memory** — `RETRIEVAL_TOP_K`, candidate K, conversation memory window.
- **CORS**, **rate limiting**, **logging** (`LOG_JSON=true`).
- **Demo bootstrap** — `BOOTSTRAP_DEMO` and the org/admin creds consumed by `bootstrap_admin.py`.

**Connects to:**
- Parsed by `app/core/config.py` for the real app, and by the hand-rolled `load_env()` in `start.py`, `verify.py`, and `db_browser.py`.
- The keys here (`POSTGRES_*`, `LLM_PROVIDER`, `API_V1_PREFIX`) drive nearly every behavior in this section.
- `docker-compose.yml` loads it via `env_file` and overrides hostnames/providers on top.
- There is also a **root `.env.example`** (the README's "Option B" copies `.env.example` → `backend\.env`) and a **`frontend/.env.example`** (`VITE_API_BASE_URL=/api/v1`, `VITE_API_PROXY=http://localhost:8000` for the dev proxy) — the backend one is the primary config the whole stack depends on.

---

### `README.md` — the runbook

**Purpose:** The human-facing operator's manual. It ties every file in this section together into copy-paste instructions for a beginner on Windows, covering prerequisites, running, seeding, databases, Docker, the API, AI setup, testing, debugging, and verification.

**How it works (structure):**
- **Project structure** — an annotated tree, including the note that `docker-compose.yml` defines "postgres, redis, chromadb, api, worker, beat, frontend."
- **Prerequisites** — Python 3.12+, Node 18+, Git, optional Docker, each with install + verify steps. Emphasizes the app runs **without Docker** via bundled PostgreSQL.
- **How to run** — *Option A* is `python start.py` (open http://localhost:5280; stop with `--stop`); *Option B* is the manual venv/uvicorn/npm flow, calling out that `seed_demo.py` loads "93 KB articles across 31 categories" while `seed_demo.py` is the 4-doc minimal seed.
- **Demo data & roles** — the four accounts (org `acme`, password `ChangeMe123!`) and the RBAC access model, plus how to test AI/notifications/user↔engineer chat.
- **Database** — the three stores and which are required vs optional (graceful degradation), how to start them, how to verify via `/health/ready`, and how to open a psql shell (Docker uses `helpdesk/helpdesk`, bundled local uses `postgres/postgres` — an important credential difference).
- **Database viewer** — documents `db_browser.py` and GUI alternatives.
- **Docker** — explains Compose in plain terms and lists the up/down/logs/reset commands; notes the single entry point is http://localhost.
- **API** — the endpoint table (health probes at the root, business routes under `/api/v1`) with curl examples, including the SSE chat call.
- **AI** — how the Gemini key is loaded (via `backend/.env` → `config.py` → provider registry, with `truststore` for corporate TLS proxies), how to verify with `check_providers.py`, how to switch providers, and how to check embeddings/retrieval.
- **Frontend / Testing / Debugging / Verification** — start the UI, run pytest with the fake provider, debug backend/frontend/Docker/AI/DB, and run `verify.py` (shows the exact PASS/FAIL checklist and that exit code is 0 only when all critical checks pass).
- **Start script** — restates what `start.py` does and its four flags.
- Closes with pointers to `docs/` and a production warning: don't use the demo `SECRET_KEY`, demo passwords, or `BOOTSTRAP_DEMO=true` in production.

**Connects to:** Every file in this section — it is the narrative wrapper that tells a developer *when* and *why* to run `start.py`, the seeders, `verify.py`, `db_browser.py`, `check_providers.py`, and `docker compose`, and how `backend/.env` (from `.env.example`) ties them all together.

---

### How the app is run, seeded, and verified — the big picture

- **Run (local):** `python start.py` → finds/starts Postgres → resolves a venv (creating `backend/.venv` if needed) → runs `seed_demo.py` → launches uvicorn (`:8000`) and Vite (`:5280`) as detached processes, logging to `logs/`. Waits on `/health` and the frontend port, then prints URLs + logins. `--stop` kills the ports and the bundled Postgres.
- **Run (Docker):** `docker compose up -d --build` → 7 services start in dependency order (infra healthy first) → each backend container runs `entrypoint.sh`, which waits for Postgres/Redis, runs `alembic upgrade head`, and (for `api`, with `BOOTSTRAP_DEMO=true`) runs `bootstrap_admin.py`, then serves uvicorn / Celery. Open **http://localhost** (nginx proxies `/api` to `api:8000`). The demo runs keyless with the `fake` provider.
- **Seed:** local uses `seed_demo.py` (schema via `create_all` + 93 articles from `demo_kb_data.py` + tickets/notifications/analytics/chat); Docker uses migrations + `bootstrap_admin.py` (org + admin only). `seed_demo.py` is the 4-doc minimal alternative; `import_bau_incidents.py` layers real Excel incidents into the KB.
- **Verify:** `python scripts/verify.py` exercises health, DB/Redis/Chroma readiness, login, a live grounded chat turn (citations + `deliver` decision), tickets, and pytest — exiting non-zero only if a *critical* component fails. `check_providers.py` is the lower-level provider smoke test, and `db_browser.py` lets you inspect the seeded rows directly.

All of these share three things: they read connection/provider config from **`backend/.env`** (templated by `.env.example`), they operate on the **same PostgreSQL schema and demo dataset**, and they treat **Redis/Chroma as optional** so the platform still runs when those aren't available.

---
