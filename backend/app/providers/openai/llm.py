"""OpenAI LLM adapter (openai>=1.x AsyncOpenAI). SDK imported lazily."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseLLMProvider, ChatMessage, LLMResult, TokenUsage


class OpenAILLMProvider(BaseLLMProvider):
    def _client(self) -> Any:
        settings = get_settings()
        api_key = settings.OPENAI_API_KEY.get_secret_value()
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is not configured.")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("openai is not installed.") from exc
        return AsyncOpenAI(api_key=api_key, timeout=self._timeout)

    async def _acomplete(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        client = self._client()
        response = await client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_output_tokens", self.max_output_tokens),
        )
        choice = response.choices[0]
        usage = response.usage
        return LLMResult(
            text=choice.message.content or "",
            model=self.model_id,
            tier=self.tier,
            finish_reason=choice.finish_reason,
            usage=TokenUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
            ),
        )

    async def _astream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        client = self._client()
        stream = await client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_output_tokens", self.max_output_tokens),
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


__all__ = ["OpenAILLMProvider"]
