"""Claude (Anthropic) concrete adapter (LLM only; embeddings via another provider)."""

from app.providers.claude.llm import ClaudeLLMProvider

__all__ = ["ClaudeLLMProvider"]
