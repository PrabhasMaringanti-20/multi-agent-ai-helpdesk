"""Tool registry — resolves tool ids and per-category tool bindings (§1.3)."""

from __future__ import annotations

from app.agents.tools import BUILTIN_TOOLS, Tool
from app.registries.category_registry import CategoryRegistry, get_category_registry


class ToolRegistry:
    def __init__(
        self,
        tools: dict[str, Tool] | None = None,
        categories: CategoryRegistry | None = None,
    ) -> None:
        self._tools = dict(tools or BUILTIN_TOOLS)
        self._categories = categories or get_category_registry()

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def for_category(self, category_key: str | None) -> list[Tool]:
        bindings = self._categories.get(category_key).tool_bindings
        return [self._tools[name] for name in bindings if name in self._tools]


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


__all__ = ["ToolRegistry", "get_tool_registry"]
