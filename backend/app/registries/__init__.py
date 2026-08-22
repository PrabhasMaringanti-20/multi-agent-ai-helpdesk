"""Data-driven extensibility registries (category / prompt / threshold / tool)."""

from app.registries.prompt_registry import PromptRegistry, get_prompt_registry

__all__ = ["PromptRegistry", "get_prompt_registry"]
