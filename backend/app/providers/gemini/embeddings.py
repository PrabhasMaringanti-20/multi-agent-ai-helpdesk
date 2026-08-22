"""Gemini embedding adapter (google-generativeai). SDK imported lazily."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseEmbeddingProvider


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    def _client(self) -> Any:
        settings = get_settings()
        api_key = settings.GEMINI_API_KEY.get_secret_value()
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not configured.")
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001
            pass
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("google-generativeai is not installed.") from exc
        genai.configure(api_key=api_key, transport="rest")
        return genai

    async def _aembed(self, texts: list[str]) -> list[list[float]]:
        genai = self._client()
        dim = self.dim

        def _embed_one(text: str) -> list[float]:
            # gemini-embedding-001 defaults to 3072 dims; request our configured
            # dimensionality (EMBEDDING_DIM) so vectors match the store/schema.
            result = genai.embed_content(
                model=self.model_id, content=text, output_dimensionality=dim
            )
            vec = [float(x) for x in result["embedding"]]
            # Reduced-dimensionality Matryoshka embeddings are not unit-normalized;
            # normalize so cosine / inner-product similarity is well-scaled.
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            return [x / norm for x in vec]

        return await asyncio.gather(*(asyncio.to_thread(_embed_one, t) for t in texts))


__all__ = ["GeminiEmbeddingProvider"]
