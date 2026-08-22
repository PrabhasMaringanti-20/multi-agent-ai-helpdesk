"""Deterministic provider doubles implementing the provider Protocols.

These make the whole engine (graph, streaming, tools, confidence) executable in
tests with no network or API keys. ``FakeLLMProvider`` supports per-schema
overrides so tests can steer routing (e.g. force a high-confidence "deliver"
path vs a low-confidence "escalate" path).
"""

from __future__ import annotations

import enum
import hashlib
import math
import uuid
from collections.abc import AsyncIterator
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from app.providers.base import (
    ChatMessage,
    EmbeddingResult,
    LLMResult,
    TokenUsage,
    VerifierResult,
)


def _fab_value(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _fab_value(args[0]) if args else None
    if origin in (list, set, tuple):
        return []
    if origin is dict:
        return {}
    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            return next(iter(annotation)).value
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, int):
            return 0
        if issubclass(annotation, float):
            return 0.9
        if issubclass(annotation, str):
            return ""
        if issubclass(annotation, uuid.UUID):
            return str(uuid.uuid4())
    return None


def _fabricate(schema: type[BaseModel]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        if field.is_required():
            values[name] = _fab_value(field.annotation)
    return values


class FakeLLMProvider:
    """Deterministic LLM double."""

    def __init__(
        self,
        *,
        model: str = "fake-llm",
        tier: str = "large",
        text: str = "Based on the knowledge base, here is the resolution [1].",
        structured: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.model_id = model
        self.tier = tier
        self._text = text
        self._structured = structured or {}

    async def generate(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResult:
        prompt_tokens = sum(len(m.content.split()) for m in messages)
        return LLMResult(
            text=self._text,
            model=self.model_id,
            tier=self.tier,
            finish_reason="stop",
            usage=TokenUsage(
                prompt_tokens, len(self._text.split()), prompt_tokens + len(self._text.split())
            ),
        )

    async def stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        for token in self._text.split(" "):
            yield token + " "

    async def generate_structured(
        self, messages: list[ChatMessage], schema: Any, **kwargs: Any
    ) -> Any:
        name = schema.__name__ if isinstance(schema, type) else "dict"
        data = self._structured.get(name)
        if data is None and isinstance(schema, type) and issubclass(schema, BaseModel):
            data = _fabricate(schema)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(data or {})
        return data or {}


class FakeEmbeddingProvider:
    """Deterministic embedding double (hash -> unit vector of length ``dim``)."""

    def __init__(self, *, model: str = "fake-embed", dim: int = 8) -> None:
        self.model_id = model
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 for i in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors = [self._vector(t) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=self.model_id,
            dim=self.dim,
            usage=TokenUsage(prompt_tokens=sum(len(t.split()) for t in texts)),
        )


class FakeVerifierProvider:
    """Deterministic verifier double based on lexical overlap."""

    def __init__(self, *, threshold: float = 0.05) -> None:
        self.threshold = threshold

    async def verify(self, claim: str, sources: list[str]) -> VerifierResult:
        claim_tokens = {t.lower() for t in claim.split() if len(t) > 3}
        source_tokens = {t.lower() for src in sources for t in src.split() if len(t) > 3}
        if not claim_tokens:
            return VerifierResult(entailed=True, score=1.0, rationale="empty claim")
        overlap = len(claim_tokens & source_tokens) / len(claim_tokens)
        return VerifierResult(
            entailed=overlap >= self.threshold,
            score=round(overlap, 4),
            rationale=f"lexical overlap={overlap:.2f}",
        )


__all__ = ["FakeLLMProvider", "FakeEmbeddingProvider", "FakeVerifierProvider"]
