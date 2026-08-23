"""Gemini embedding adapter (google-genai, the unified SDK). SDK imported lazily.

``google-generativeai`` (used previously) was retired by Google - all support
ended 2025-11-30. This adapter uses the current unified SDK instead.
"""

from __future__ import annotations

import math
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ProviderError
from app.providers.base import BaseEmbeddingProvider


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    def _client(self) -> tuple[Any, Any]:
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
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("google-genai is not installed.") from exc
        return genai.Client(api_key=api_key), types

    async def _aembed(self, texts: list[str]) -> list[list[float]]:
        client, types = self._client()
        dim = self.dim

        # gemini-embedding-001 defaults to 3072 dims; request our configured
        # dimensionality (EMBEDDING_DIM) so vectors match the store/schema.
        response = await client.aio.models.embed_content(
            model=self.model_id,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=dim),
        )

        vectors: list[list[float]] = []
        for embedding in response.embeddings:
            vec = [float(x) for x in embedding.values]
            # Reduced-dimensionality Matryoshka embeddings are not unit-normalized;
            # normalize so cosine / inner-product similarity is well-scaled.
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return vectors


__all__ = ["GeminiEmbeddingProvider"]
