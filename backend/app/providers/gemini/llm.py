"""Gemini LLM adapter (google-genai, the unified SDK). SDK imported lazily.

``google-generativeai`` (the SDK this adapter used previously) was retired by
Google - all support for that package ended 2025-11-30. This adapter uses the
current unified SDK (``google-genai``) instead: ``from google import genai``,
``genai.Client(api_key=...)``, and the async surface under ``client.aio``.

Uses ``truststore`` (OS trust store) so it works behind corporate
TLS-inspection proxies. The engine streams the final answer itself (the
synthesizer calls ``generate``), so the non-streaming path is what actually
matters; ``_astream`` uses the SDK's real async streaming call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseLLMProvider, ChatMessage, LLMResult, TokenUsage


def _to_gemini(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts = [m.content for m in messages if m.role == "system"]
    system = "\n\n".join(system_parts) or None
    contents = [
        {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
        for m in messages
        if m.role in ("user", "assistant")
    ]
    return system, contents


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    collected: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            chunk = getattr(part, "text", None)
            if chunk:
                collected.append(chunk)
    return "".join(collected)


class GeminiLLMProvider(BaseLLMProvider):
    def _client(self) -> tuple[Any, Any]:
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
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("google-genai is not installed.") from exc
        return genai.Client(api_key=api_key), types

    def _generation_config(self, types: Any, system: str | None, kwargs: dict[str, Any]) -> Any:
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=kwargs.get("temperature", self.temperature),
            max_output_tokens=kwargs.get("max_output_tokens", self.max_output_tokens),
        )

    async def _acomplete(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        client, types = self._client()
        system, contents = _to_gemini(messages)
        config = self._generation_config(types, system, kwargs)

        response = await client.aio.models.generate_content(
            model=self.model_id, contents=contents, config=config
        )
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=_extract_text(response),
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
        client, types = self._client()
        system, contents = _to_gemini(messages)
        config = self._generation_config(types, system, kwargs)

        stream = await client.aio.models.generate_content_stream(
            model=self.model_id, contents=contents, config=config
        )
        async for chunk in stream:
            text = _extract_text(chunk)
            if text:
                yield text


__all__ = ["GeminiLLMProvider"]
