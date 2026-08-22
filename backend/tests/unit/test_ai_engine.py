"""AI-engine test matrix (Phase 12): compilation, reachability, execution, stream.

Runs the whole graph with fake providers/services (no network, no DB): the
deliver path (auto-resolve) and the escalate path (handoff + ticket), streaming
+ cancellation, the learning subgraph, provider loading, and DI assembly.
"""

from __future__ import annotations

import types
import uuid

import pytest
from app.agents.config_schema import GraphDeps, build_config
from app.agents.engine import HelpdeskAIEngine
from app.agents.graph import compile_graph
from app.agents.learning_graph import compile_learning_graph
from app.agents.state import Citation, MemoryState, RetrievedChunk
from app.agents.streaming import CancellationToken
from app.core.config import get_settings
from app.core.constants import Decision
from app.providers.base import VerifierResult
from app.providers.fakes import FakeEmbeddingProvider, FakeLLMProvider
from app.registries.category_registry import get_category_registry
from app.registries.prompt_registry import get_prompt_registry
from app.registries.threshold_registry import get_threshold_registry
from app.registries.tool_registry import get_tool_registry
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START

CANONICAL_NODES = {
    "ingress_guard",
    "memory_manager",
    "intent_classifier",
    "query_planner",
    "rag_retriever",
    "retrieval_gate",
    "solution_synthesizer",
    "grounding_verifier",
    "confidence_gate",
    "info_collector",
    "ticket_creator",
    "human_handoff",
    "responder",
}


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeVerifier:
    def __init__(self, entailed: bool = True, score: float = 0.95) -> None:
        self.entailed, self.score = entailed, score

    async def verify(self, claim, sources):
        return VerifierResult(entailed=self.entailed, score=self.score)


class FakeRetriever:
    def __init__(self, outcome):
        self.outcome = outcome

    async def retrieve(self, *, query, org_id, namespace=None, category=None):
        return self.outcome


class FakeMemory:
    async def load_state(self, *, user_id, conversation_id):
        return MemoryState()

    async def persist_turn(self, *, org_id, user_id, conversation_id, turn_id, state):
        return state


class FakeTickets:
    def __init__(self):
        self.created = []

    async def create_from_conversation(self, **kw):
        self.created.append(kw)
        return types.SimpleNamespace(id=uuid.uuid4(), assigned_queue="billing")


class FakeNotifications:
    def __init__(self):
        self.calls = []

    async def notify_engineer(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(id=uuid.uuid4())


class FakeAnalytics:
    def __init__(self):
        self.events = []

    async def record(self, **kw):
        self.events.append(kw)
        return types.SimpleNamespace(id=uuid.uuid4())


class FakeAudit:
    async def record(self, **kw):
        return types.SimpleNamespace(id=uuid.uuid4())


def make_deps(
    *,
    answer="Reset the VPN client and reconnect [1].",
    entailed=True,
    tickets=None,
    notifications=None,
    analytics=None,
    empty_retrieval=False,
) -> GraphDeps:
    if empty_retrieval:
        # Genuinely unanswerable: nothing retrieved, so even the L2 assist tier
        # (broad search) finds no candidates and the turn escalates to L3.
        outcome = types.SimpleNamespace(
            candidates=[],
            context="",
            citations=[],
            max_relevance_score=0.0,
        )
    else:
        outcome = types.SimpleNamespace(
            candidates=[
                RetrievedChunk(
                    chunk_id="c1", doc_id="d1", text="reset the vpn client and reconnect"
                )
            ],
            context="[1] reset the vpn client and reconnect",
            citations=[Citation(chunk_id="c1", doc_id="d1")],
            max_relevance_score=0.8,
        )
    return GraphDeps(
        settings=get_settings(),
        llm_large=FakeLLMProvider(text=answer),
        llm_small=FakeLLMProvider(
            text="standalone query",
            structured={
                "IntentResult": {
                    "category": "general",
                    "intent": "support_request",
                    "intent_confidence": 0.95,
                    "sensitivity_level": "low",
                    "control_intent": None,
                }
            },
        ),
        embedder=FakeEmbeddingProvider(),
        verifier=FakeVerifier(entailed=entailed),
        retriever=FakeRetriever(outcome),
        memory=FakeMemory(),
        kb=None,
        tickets=tickets or FakeTickets(),
        notifications=notifications or FakeNotifications(),
        analytics=analytics or FakeAnalytics(),
        feedback=None,
        audit=FakeAudit(),
        prompts=get_prompt_registry(),
        categories=get_category_registry(),
        thresholds=get_threshold_registry(),
        tools=get_tool_registry(),
    )


def _ids() -> dict[str, str]:
    return {
        "thread_id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "turn_id": 1,
    }


# --------------------------------------------------------------------------- #
# Graph structure
# --------------------------------------------------------------------------- #
def test_graph_compiles() -> None:
    compiled = compile_graph(MemorySaver())
    assert compiled is not None


def test_no_dead_or_unreachable_nodes() -> None:
    graph = compile_graph(MemorySaver()).get_graph()
    forward: dict[str, set[str]] = {}
    backward: dict[str, set[str]] = {}
    for edge in graph.edges:
        forward.setdefault(edge.source, set()).add(edge.target)
        backward.setdefault(edge.target, set()).add(edge.source)

    def reach(adj, start):
        seen, stack = set(), [start]
        while stack:
            node = stack.pop()
            for nxt in adj.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    reachable_from_start = reach(forward, START)
    can_reach_end = reach(backward, END)
    assert reachable_from_start >= CANONICAL_NODES, CANONICAL_NODES - reachable_from_start
    assert can_reach_end >= CANONICAL_NODES, CANONICAL_NODES - can_reach_end


def test_provider_loading() -> None:
    from app.providers.registry import get_llm_provider

    llm = get_llm_provider("large")
    assert hasattr(llm, "generate") and hasattr(llm, "stream")


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_deliver_path() -> None:
    engine = HelpdeskAIEngine()
    final = await engine.run(
        deps=make_deps(entailed=True), user_message="how do I fix my vpn", **_ids()
    )
    assert final["decision"] == Decision.DELIVER
    assert "Reset the VPN" in final["response_text"]
    assert {"solution_synthesizer", "grounding_verifier", "confidence_gate", "responder"} <= set(
        final["node_path"]
    )
    assert final["final_confidence"] and final["final_confidence"] >= 0.75


@pytest.mark.asyncio
async def test_escalate_path_creates_ticket_and_notifies() -> None:
    engine = HelpdeskAIEngine()
    tickets, notifications = FakeTickets(), FakeNotifications()
    final = await engine.run(
        deps=make_deps(
            answer="ABSTAIN",
            entailed=False,
            empty_retrieval=True,
            tickets=tickets,
            notifications=notifications,
        ),
        user_message="my payment failed and nothing works",
        **_ids(),
    )
    assert final["decision"] == Decision.ESCALATE
    assert final["ticket_id"] and tickets.created and notifications.calls
    assert "ticket" in final["response_text"].lower()
    assert {"ticket_creator", "human_handoff", "responder"} <= set(final["node_path"])


@pytest.mark.asyncio
async def test_no_runtime_exceptions_both_paths() -> None:
    engine = HelpdeskAIEngine()
    for entailed in (True, False):
        final = await engine.run(deps=make_deps(entailed=entailed), user_message="help", **_ids())
        assert final.get("error") is None
        assert final["response_text"]


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_streaming_emits_tokens_and_done() -> None:
    engine = HelpdeskAIEngine()
    events = [e async for e in engine.astream(deps=make_deps(), user_message="fix vpn", **_ids())]
    kinds = [e.type.value for e in events]
    assert "typing" in kinds and "token" in kinds and "done" in kinds
    assert kinds[0] == "typing"


@pytest.mark.asyncio
async def test_streaming_cancellation() -> None:
    engine = HelpdeskAIEngine()
    token = CancellationToken()
    token.cancel()
    events = [
        e
        async for e in engine.astream(
            deps=make_deps(), user_message="fix vpn", cancel_token=token, **_ids()
        )
    ]
    assert any(e.type.value == "cancelled" for e in events)


# --------------------------------------------------------------------------- #
# Learning subgraph + DI
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_learning_graph_runs() -> None:
    graph = compile_learning_graph()
    deps = make_deps()
    out = await graph.ainvoke(
        {
            "trigger": "engineer_resolved",
            "org_id": str(uuid.uuid4()),
            "source_text": "To fix vpn reset the client and reconnect. Steps: open app, reset, reconnect.",
        },
        build_config(deps, thread_id="learn-1"),
    )
    assert out["status"] == "upserted" and out["chunk_count"] >= 1


def test_di_assembles_graph_deps() -> None:
    from app.api.deps import get_graph_deps

    deps = get_graph_deps(session=object())  # stub session; repos lazy, no connection
    for field in (
        "llm_large",
        "llm_small",
        "embedder",
        "verifier",
        "retriever",
        "memory",
        "kb",
        "tickets",
        "notifications",
        "analytics",
        "feedback",
        "audit",
        "prompts",
        "categories",
        "thresholds",
        "tools",
    ):
        assert getattr(deps, field) is not None, field
