"""Multi-provider fallback LLM wrapper.

Wraps an ordered list of LLM providers and tries them in turn: when one raises a
``ProviderError`` (e.g. quota / 429), the next is attempted. This lets the app
survive a single provider's quota exhaustion *when a second provider is
configured* (``LLM_FALLBACK_PROVIDERS``), without any node knowing about
fallback — it satisfies the same interface as a single provider. With no
fallback configured the registry returns the bare primary, so behaviour is
unchanged by default.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.exceptions import ProviderError
from app.core.logging import get_logger
from app.providers.base import ChatMessage, LLMProvider, LLMResult

_logger = get_logger(__name__)


class FallbackLLMProvider:
    """Try each wrapped provider in order until one succeeds."""

    def __init__(self, providers: list[LLMProvider], *, tier: str) -> None:
        if not providers:
            raise ProviderError("FallbackLLMProvider requires at least one provider.")
        self._providers = providers
        self.tier = tier
        self.model_id = getattr(providers[0], "model_id", "fallback")

    def __getattr__(self, name: str) -> Any:
        # Delegate any attribute we don't define (temperature, max_output_tokens,
        # etc.) to the primary provider. Only called on normal-lookup miss.
        return getattr(self.__dict__["_providers"][0], name)

    async def generate(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        last: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.generate(messages, **kwargs)
            except ProviderError as exc:
                last = exc
                _logger.warning(
                    "LLM provider %s failed; trying next in fallback chain: %s",
                    getattr(provider, "model_id", provider),
                    exc,
                )
        raise last or ProviderError("All LLM providers in the fallback chain failed.")

    async def generate_structured(
        self, messages: list[ChatMessage], schema: Any, **kwargs: Any
    ) -> Any:
        last: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.generate_structured(messages, schema, **kwargs)
            except ProviderError as exc:
                last = exc
                _logger.warning(
                    "LLM provider %s (structured) failed; trying next: %s",
                    getattr(provider, "model_id", provider),
                    exc,
                )
        raise last or ProviderError("All LLM providers in the fallback chain failed.")

    async def stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        last: Exception | None = None
        for provider in self._providers:
            try:
                async for token in provider.stream(messages, **kwargs):
                    yield token
                return
            except ProviderError as exc:
                last = exc
                _logger.warning(
                    "LLM provider %s (stream) failed; trying next: %s",
                    getattr(provider, "model_id", provider),
                    exc,
                )
        if last:
            raise last


__all__ = ["FallbackLLMProvider"]
