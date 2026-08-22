"""OpenAI embedding adapter (openai>=1.x AsyncOpenAI). SDK imported lazily."""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseEmbeddingProvider


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
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

    async def _aembed(self, texts: list[str]) -> list[list[float]]:
        client = self._client()
        response = await client.embeddings.create(model=self.model_id, input=texts)
        return [list(item.embedding) for item in response.data]


__all__ = ["OpenAIEmbeddingProvider"]
