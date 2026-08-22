"""Feedback-learner subgraph (async, event-triggered) — ARCHITECTURE.md §1.2.

Runs out-of-band from a worker when an engineer resolves a ticket, a user gives
feedback, or an admin uploads a doc: draft -> approval_gate -> kb_upsert. The
draft is produced by the LLM, gated for approval, then chunked for KB ingestion
(the actual vector upsert is performed by the ingestion worker/Chroma; here we
prepare and record the chunks). Dependencies are injected via ``config`` exactly
like the main graph.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents.config_schema import get_deps
from app.agents.state import merge_lists
from app.core.constants import LearningStatus
from app.rag.chunker import chunk_text


class LearningState(TypedDict, total=False):
    trigger: str
    org_id: str
    source_text: str
    category: str
    draft: str
    approved: bool
    chunk_count: int
    status: str
    node_path: Annotated[list[str], merge_lists]


async def draft_node(state: LearningState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    from app.providers.base import ChatMessage

    messages = [
        ChatMessage(
            role="system",
            content="Draft a concise, reusable KB article from the resolution below. Include a title and clear steps.",
        ),
        ChatMessage(role="user", content=state.get("source_text", "")),
    ]
    try:
        result = await deps.llm_large.generate(messages)
        draft = result.text.strip()
    except Exception:  # noqa: BLE001
        draft = state.get("source_text", "")
    return {"draft": draft, "status": LearningStatus.DRAFTED.value, "node_path": ["draft"]}


async def approval_gate(state: LearningState, config: RunnableConfig) -> dict[str, Any]:
    approved = bool(state.get("draft", "").strip())
    status = LearningStatus.APPROVED.value if approved else LearningStatus.REJECTED.value
    return {"approved": approved, "status": status, "node_path": ["approval_gate"]}


async def kb_upsert(state: LearningState, config: RunnableConfig) -> dict[str, Any]:
    chunks = chunk_text(state.get("draft", ""))
    return {
        "chunk_count": len(chunks),
        "status": LearningStatus.UPSERTED.value,
        "node_path": ["kb_upsert"],
    }


def _route_after_approval(state: LearningState) -> str:
    return "upsert" if state.get("approved") else "end"


def build_learning_graph() -> StateGraph:
    graph: StateGraph = StateGraph(LearningState)
    graph.add_node("draft", draft_node)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("kb_upsert", kb_upsert)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "approval_gate")
    graph.add_conditional_edges(
        "approval_gate", _route_after_approval, {"upsert": "kb_upsert", "end": END}
    )
    graph.add_edge("kb_upsert", END)
    return graph


def compile_learning_graph() -> Any:
    return build_learning_graph().compile()


__all__ = ["LearningState", "build_learning_graph", "compile_learning_graph"]
