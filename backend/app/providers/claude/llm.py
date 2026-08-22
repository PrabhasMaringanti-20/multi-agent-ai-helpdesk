"""Claude LLM adapter (anthropic AsyncAnthropic). SDK imported lazily."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseLLMProvider, ChatMessage, LLMResult, TokenUsage


def _split_system(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, str]]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system") or None
    turns = [
        {"role": "assistant" if m.role == "assistant" else "user", "content": m.content}
        for m in messages
        if m.role in ("user", "assistant")
    ]
    return system, turns


class ClaudeLLMProvider(BaseLLMProvider):
    def _client(self) -> Any:
        settings = get_settings()
        api_key = settings.ANTHROPIC_API_KEY.get_secret_value()
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured.")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("anthropic is not installed.") from exc
        return AsyncAnthropic(api_key=api_key, timeout=self._timeout)

    async def _acomplete(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        client = self._client()
        system, turns = _split_system(messages)
        response = await client.messages.create(
            model=self.model_id,
            system=system or "",
            messages=turns,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_output_tokens", self.max_output_tokens),
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return LLMResult(
            text=text,
            model=self.model_id,
            tier=self.tier,
            finish_reason=response.stop_reason,
            usage=TokenUsage(
                prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(usage, "output_tokens", 0) or 0,
                total_tokens=(getattr(usage, "input_tokens", 0) or 0)
                + (getattr(usage, "output_tokens", 0) or 0),
            ),
        )

    async def _astream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        client = self._client()
        system, turns = _split_system(messages)
        async with client.messages.stream(
            model=self.model_id,
            system=system or "",
            messages=turns,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_output_tokens", self.max_output_tokens),
        ) as stream:
            async for text in stream.text_stream:
                yield text


__all__ = ["ClaudeLLMProvider"]
