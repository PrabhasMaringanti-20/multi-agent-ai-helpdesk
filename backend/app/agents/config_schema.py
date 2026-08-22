"""Graph dependency contract (injected via LangGraph ``config``).

Per ARCHITECTURE.md §5.2, provider/service handles are passed into the graph via
the LangGraph ``config['configurable']`` map, never stored in ``AgentState`` (so
state stays JSON-serializable for the Postgres checkpointer). ``GraphDeps`` is
that handle bundle; nodes call ``get_deps(config)`` to reach it. It is duck-typed
(``Any`` fields) so tests can inject fakes for every collaborator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GraphDeps:
    """Everything the nodes need, assembled per-request by DI (or by tests)."""

    settings: Any
    llm_large: Any
    llm_small: Any
    embedder: Any
    verifier: Any
    retriever: Any
    memory: Any
    kb: Any
    tickets: Any
    notifications: Any
    analytics: Any
    feedback: Any
    audit: Any
    prompts: Any
    categories: Any
    thresholds: Any
    tools: Any
    users: Any = None
    conversations: Any = None
    redis: Any = None
    uploads: Any = None  # searcher over user-attached documents (Document Search)


def build_config(deps: GraphDeps, *, thread_id: str) -> dict[str, Any]:
    """Build the LangGraph RunnableConfig carrying the dependency bundle."""
    return {"configurable": {"deps": deps, "thread_id": thread_id}}


def get_deps(config: Any) -> GraphDeps:
    """Extract the ``GraphDeps`` bundle from a LangGraph config, or raise."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    deps = configurable.get("deps")
    if not isinstance(deps, GraphDeps):
        raise RuntimeError(
            "GraphDeps missing: pass build_config(deps, thread_id=...) as the graph config."
        )
    return deps


__all__ = ["GraphDeps", "build_config", "get_deps"]
