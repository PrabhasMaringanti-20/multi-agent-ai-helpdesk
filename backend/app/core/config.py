"""Application configuration.

Per the approved architecture (ARCHITECTURE.md §4/§5.2) all configuration is
consolidated in the canonical ``core.config`` module. ``Settings`` is an
env-driven Pydantic v2 ``BaseSettings`` object (12-factor); ``get_settings()``
returns a cached singleton that is injected via FastAPI's dependency system and
imported directly by lower layers that run outside a request (workers, CLI).

Validation is fail-fast: a production deployment that ships the insecure
development ``SECRET_KEY`` (or a missing Gemini key while Gemini is the active
provider) raises at construction time rather than starting in an unsafe state.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import quote_plus

from pydantic import (
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.constants import Environment

_INSECURE_DEV_SECRETS: frozenset[str] = frozenset(
    {
        "change-me-please-use-a-32char-min-random-secret",
        "dev-insecure-local-secret-key-change-me-0123456789",
    }
)


class Settings(BaseSettings):
    """Strongly typed, env-driven application settings."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application ----
    APP_NAME: str = "Enterprise Multi-Agent AI Helpdesk Platform"
    APP_ENV: Environment = Environment.LOCAL
    API_V1_PREFIX: str = "/api/v1"
    VERSION: str = "1.0.0"

    # ---- Security / JWT ----
    SECRET_KEY: SecretStr = SecretStr("dev-insecure-local-secret-key-change-me-0123456789")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    JWT_ISSUER: str = "helpdesk-platform"
    JWT_AUDIENCE: str = "helpdesk-clients"

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "helpdesk"
    POSTGRES_PASSWORD: SecretStr = SecretStr("helpdesk")
    POSTGRES_DB: str = "helpdesk"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # ---- Redis (required infrastructure) ----
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---- Celery (Redis broker) ----
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ---- Vector store ----
    # "pg" = local Postgres brute-force store (no server, no downloads; default);
    # "chromadb" = HTTP Chroma server at CHROMA_HOST:CHROMA_PORT.
    VECTOR_STORE_BACKEND: str = "pg"
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000
    CHROMA_KB_COLLECTION: str = "kb_chunks"
    CHROMA_KB_PENDING_COLLECTION: str = "kb_chunks_pending"

    # ---- LLM / Embedding providers (abstract; Gemini default, swappable) ----
    LLM_PROVIDER: str = "gemini"  # gemini | openai | claude | fake
    # Ordered fallback chain tried when the primary provider errors (e.g. quota/
    # 429). Comma-separated provider names, e.g. "openai,claude". Empty = no
    # fallback (single provider). Each listed provider needs its own API key set.
    LLM_FALLBACK_PROVIDERS: str = ""
    EMBEDDING_PROVIDER: str = "gemini"  # gemini | openai | fake
    GEMINI_API_KEY: SecretStr = SecretStr("")
    LLM_SMALL_MODEL: str = "gemini-1.5-flash"
    LLM_LARGE_MODEL: str = "gemini-1.5-pro"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIM: int = 768

    # OpenAI (optional; used when LLM_PROVIDER/EMBEDDING_PROVIDER = openai)
    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_SMALL_MODEL: str = "gpt-4o-mini"
    OPENAI_LARGE_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Anthropic / Claude (optional; used when LLM_PROVIDER = claude)
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_SMALL_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_LARGE_MODEL: str = "claude-sonnet-5"

    # Generation defaults + resilience
    LLM_TEMPERATURE: float = 0.2
    LLM_MAX_OUTPUT_TOKENS: int = 1024
    LLM_TIMEOUT_SECONDS: int = 30
    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BASE_DELAY: float = 0.5
    LLM_RATE_LIMIT_PER_MINUTE: int = 300

    # Retrieval + memory defaults
    RETRIEVAL_TOP_K: int = 6
    RETRIEVAL_CANDIDATE_K: int = 20
    MEMORY_WINDOW_TURNS: int = 10
    MEMORY_SUMMARY_TRIGGER_TURNS: int = 12

    # ---- CORS ----
    # NoDecode: disable pydantic-settings' automatic JSON decoding so the
    # validator below can accept a comma-separated string from .env / env vars.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )

    # ---- Rate limiting ----
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 120

    # ---- Logging ----
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("APP_ENV", mode="before")
    @classmethod
    def _normalize_env(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        """Accept either a JSON array or a comma-separated string from env."""
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return []
            if candidate.startswith("["):
                import json

                return json.loads(candidate)
            return [origin.strip() for origin in candidate.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        if self.APP_ENV != Environment.PRODUCTION:
            return self
        secret = self.SECRET_KEY.get_secret_value()
        if secret in _INSECURE_DEV_SECRETS or len(secret) < 32:
            raise ValueError(
                "SECRET_KEY must be a unique random value of >= 32 chars in production."
            )
        if self.LLM_PROVIDER == "gemini" and not self.GEMINI_API_KEY.get_secret_value():
            raise ValueError("GEMINI_API_KEY is required in production when LLM_PROVIDER=gemini.")
        return self

    # ------------------------------------------------------------------ #
    # Derived values
    # ------------------------------------------------------------------ #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_async_dsn(self) -> str:
        """Async DSN for the application engine (asyncpg driver)."""
        password = quote_plus(self.POSTGRES_PASSWORD.get_secret_value())
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_sync_dsn(self) -> str:
        """Sync DSN for Alembic migrations (psycopg driver)."""
        password = quote_plus(self.POSTGRES_PASSWORD.get_secret_value())
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, process-wide settings singleton."""
    return Settings()


# Eagerly constructed convenience singleton. Safe to import at module load
# because every field has a development default; production safety is enforced
# by the model validator above.
settings: Settings = get_settings()

__all__ = ["Settings", "get_settings", "settings"]
