"""Gemini LLM adapter (google-generativeai). SDK imported lazily.

Uses the REST transport + ``truststore`` (OS trust store) so it works behind
corporate TLS-inspection proxies. The engine streams the final answer itself
(the synthesizer calls ``generate``), so the REST-only sync path is sufficient;
``_astream`` emits the full completion as one chunk if ever called directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseLLMProvider, ChatMessage, LLMResult, TokenUsage


def _to_gemini(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts = [m.content for m in messages if m.role == "system"]
    system = "\n\n".join(system_parts) or None
    contents = [
        {"role": "model" if m.role == "assistant" else "user", "parts": [m.content]}
        for m in messages
        if m.role in ("user", "assistant")
    ]
    return system, contents


class GeminiLLMProvider(BaseLLMProvider):
    def _client(self) -> Any:
        settings = get_settings()
        api_key = settings.GEMINI_API_KEY.get_secret_value()
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not configured.")
        # Trust the OS certificate store (handles corporate TLS inspection).
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001 - best-effort; fall back to certifi
            pass
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("google-generativeai is not installed.") from exc
        # REST transport respects Python's ssl (and thus truststore); gRPC does not.
        genai.configure(api_key=api_key, transport="rest")
        return genai

    def _generation_config(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "temperature": kwargs.get("temperature", self.temperature),
            "max_output_tokens": kwargs.get("max_output_tokens", self.max_output_tokens),
        }

    async def _acomplete(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        genai = self._client()
        system, contents = _to_gemini(messages)
        model = genai.GenerativeModel(self.model_id, system_instruction=system)
        config = self._generation_config(kwargs)

        def _call() -> Any:
            return model.generate_content(contents, generation_config=config)

        response = await asyncio.to_thread(_call)
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=response.text or "",
            model=self.model_id,
            tier=self.tier,
            finish_reason="stop",
            usage=TokenUsage(
                prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                total_tokens=getattr(usage, "total_token_count", 0) or 0,
            ),
        )

    async def _astream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        result = await self._acomplete(messages, **kwargs)
        yield result.text


__all__ = ["GeminiLLMProvider"]
