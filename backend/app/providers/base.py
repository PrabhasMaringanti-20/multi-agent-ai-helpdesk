"""Provider abstraction: contracts, resilience, and abstract base classes.

Defines the swappable provider Protocols (``LLMProvider`` / ``EmbeddingProvider``
/ ``VerifierProvider``) from ARCHITECTURE.md §5.2, plus the cross-cutting
resilience concerns every concrete adapter shares: an async per-minute rate
limiter, bounded exponential retry, per-model token accounting, and a timeout.
Concrete adapters (Gemini/OpenAI/Claude) subclass the base classes and implement
only the raw ``_acomplete`` / ``_astream`` / ``_aembed`` calls (lazily importing
their SDKs). No API keys are ever hardcoded — adapters read them from settings.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from app.core.exceptions import ProviderError
from app.core.logging import get_logger

_logger = get_logger(__name__)
_T = TypeVar("_T")


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    tier: str = "large"
    finish_reason: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dim: int
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class VerifierResult:
    entailed: bool
    score: float
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Protocols (structural contracts used for typing / DI)
# --------------------------------------------------------------------------- #
@runtime_checkable
class LLMProvider(Protocol):
    model_id: str
    tier: str

    async def generate(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult: ...

    def stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]: ...

    async def generate_structured(
        self, messages: list[ChatMessage], schema: Any, **kwargs: Any
    ) -> Any: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_id: str
    dim: int

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...


@runtime_checkable
class VerifierProvider(Protocol):
    async def verify(self, claim: str, sources: list[str]) -> VerifierResult: ...


# --------------------------------------------------------------------------- #
# Resilience utilities
# --------------------------------------------------------------------------- #
class AsyncRateLimiter:
    """Simple async sliding-window limiter (``max_per_minute`` calls / 60s)."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.max_per_minute <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            while self._events and now - self._events[0] >= 60.0:
                self._events.popleft()
            if len(self._events) >= self.max_per_minute:
                sleep_for = 60.0 - (now - self._events[0])
                _logger.warning("LLM rate limit reached; sleeping %.2fs", sleep_for)
                await asyncio.sleep(max(0.0, sleep_for))
            self._events.append(time.monotonic())


class TokenAccountant:
    """Accumulates token usage per model for cost attribution."""

    def __init__(self) -> None:
        self._usage: dict[str, TokenUsage] = {}
        self._lock = asyncio.Lock()

    async def record(self, model: str, usage: TokenUsage) -> None:
        async with self._lock:
            self._usage[model] = self._usage.get(model, TokenUsage()) + usage

    def snapshot(self) -> dict[str, TokenUsage]:
        return dict(self._usage)

    @property
    def total(self) -> TokenUsage:
        total = TokenUsage()
        for usage in self._usage.values():
            total = total + usage
        return total


async def retry_async(
    factory: Callable[[], Awaitable[_T]],
    *,
    retries: int,
    base_delay: float,
    what: str,
) -> _T:
    """Run ``factory()`` with bounded exponential backoff + jitter."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await factory()
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - transient provider failures
            last_exc = exc
            if attempt >= retries:
                break
            delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
            _logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.2fs",
                what,
                attempt + 1,
                retries + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise ProviderError(f"{what} failed after {retries + 1} attempts: {last_exc}")


# --------------------------------------------------------------------------- #
# Abstract base adapters
# --------------------------------------------------------------------------- #
class BaseLLMProvider(ABC):
    """Base LLM adapter wiring rate limiting, retry, timeout, and accounting."""

    def __init__(
        self,
        *,
        model: str,
        tier: str,
        rate_limiter: AsyncRateLimiter,
        accountant: TokenAccountant,
        timeout: float,
        max_retries: int,
        base_delay: float,
        temperature: float,
        max_output_tokens: int,
    ) -> None:
        self.model_id = model
        self.tier = tier
        self._rate_limiter = rate_limiter
        self._accountant = accountant
        self._timeout = timeout
        self._max_retries = max_retries
        self._base_delay = base_delay
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    @abstractmethod
    async def _acomplete(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult: ...

    @abstractmethod
    def _astream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]: ...

    async def generate(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        await self._rate_limiter.acquire()

        async def _call() -> LLMResult:
            return await asyncio.wait_for(
                self._acomplete(messages, **kwargs), timeout=self._timeout
            )

        result = await retry_async(
            _call,
            retries=self._max_retries,
            base_delay=self._base_delay,
            what=f"{type(self).__name__}.generate({self.model_id})",
        )
        await self._accountant.record(self.model_id, result.usage)
        return result

    async def stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        await self._rate_limiter.acquire()
        try:
            async for token in self._astream(messages, **kwargs):
                yield token
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"{type(self).__name__}.stream({self.model_id}) failed: {exc}"
            ) from exc

    async def generate_structured(
        self, messages: list[ChatMessage], schema: Any, **kwargs: Any
    ) -> Any:
        """Ask for a single JSON object and validate it against ``schema``."""
        schema_hint = _schema_hint(schema)
        instruction = ChatMessage(
            role="system",
            content=(
                "Respond with ONLY a single valid JSON object, no prose, no code "
                f"fences. It must match this schema: {schema_hint}"
            ),
        )
        result = await self.generate([instruction, *messages], **kwargs)
        payload = _extract_json(result.text)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(payload)
        return payload


class BaseEmbeddingProvider(ABC):
    """Base embedding adapter wiring rate limiting, retry, and accounting."""

    def __init__(
        self,
        *,
        model: str,
        dim: int,
        rate_limiter: AsyncRateLimiter,
        accountant: TokenAccountant,
        timeout: float,
        max_retries: int,
        base_delay: float,
    ) -> None:
        self.model_id = model
        self.dim = dim
        self._rate_limiter = rate_limiter
        self._accountant = accountant
        self._timeout = timeout
        self._max_retries = max_retries
        self._base_delay = base_delay

    @abstractmethod
    async def _aembed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        await self._rate_limiter.acquire()

        async def _call() -> list[list[float]]:
            return await asyncio.wait_for(self._aembed(texts), timeout=self._timeout)

        vectors = await retry_async(
            _call,
            retries=self._max_retries,
            base_delay=self._base_delay,
            what=f"{type(self).__name__}.embed({self.model_id})",
        )
        usage = TokenUsage(prompt_tokens=sum(len(t.split()) for t in texts))
        await self._accountant.record(self.model_id, usage)
        return EmbeddingResult(vectors=vectors, model=self.model_id, dim=self.dim, usage=usage)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _schema_hint(schema: Any) -> str:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return json.dumps(schema.model_json_schema())
    if isinstance(schema, dict):
        return json.dumps(schema)
    return str(schema)


def _extract_json(text: str) -> Any:
    """Extract the first JSON object from an LLM response, tolerating fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ProviderError("LLM did not return a JSON object.")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"LLM returned invalid JSON: {exc}") from exc


__all__ = [
    "ChatMessage",
    "TokenUsage",
    "LLMResult",
    "EmbeddingResult",
    "VerifierResult",
    "LLMProvider",
    "EmbeddingProvider",
    "VerifierProvider",
    "AsyncRateLimiter",
    "TokenAccountant",
    "retry_async",
    "BaseLLMProvider",
    "BaseEmbeddingProvider",
]
