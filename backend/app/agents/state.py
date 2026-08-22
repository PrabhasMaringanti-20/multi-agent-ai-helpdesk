"""AgentState — the single, versioned shared contract for the LangGraph workflow.

Per ARCHITECTURE.md §1.4 the graph carries ONE state object. It is modelled here
as a ``TypedDict`` (LangGraph's native state type) whose grouped concerns are
typed Pydantic sub-models (Conversation/Execution/Memory/Retrieval/Approval/
Streaming state). Append-style keys (``messages``, ``node_path``, ``audit_trail``)
use list-merge reducers so concurrent/steppy writes accumulate; every other key
is last-write-wins. Provider/service handles are NEVER stored here — they are
injected via the LangGraph ``config`` (see ``config_schema``) to keep state
serializable for the Postgres checkpointer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

from app.core.constants import Decision, SensitivityLevel
from app.core.utils import utcnow


# --------------------------------------------------------------------------- #
# Reducers
# --------------------------------------------------------------------------- #
def merge_lists(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Reducer that concatenates two list values (append semantics)."""
    return (list(left) if left else []) + (list(right) if right else [])


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
class RetrievedChunk(BaseModel):
    """A single retrieval candidate mirroring a ``kb_chunks`` row + score."""

    chunk_id: str
    doc_id: str
    text: str
    score: float = 0.0
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None
    source_uri: str | None = None
    version: int | None = None
    category_key: str | None = None
    last_verified_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    """A citation attached to an answer (subset of a chunk's provenance)."""

    chunk_id: str
    doc_id: str
    source_uri: str | None = None
    version: int | None = None
    quote: str | None = None


# --------------------------------------------------------------------------- #
# Grouped sub-states
# --------------------------------------------------------------------------- #
class ConversationState(BaseModel):
    """Intent, category, and slot-filling status for the current turn."""

    category: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    sensitivity_level: SensitivityLevel = SensitivityLevel.LOW
    control_intent: str | None = None  # greeting | cancel | human_request | None
    required_slots: list[str] = Field(default_factory=list)
    filled_slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)


class ExecutionContext(BaseModel):
    """Per-run execution metadata and budgets."""

    trace_id: str
    turn_id: int
    started_at: datetime = Field(default_factory=utcnow)
    model_tiers: dict[str, str] = Field(default_factory=dict)
    node_timings_ms: dict[str, float] = Field(default_factory=dict)
    retry_budget: int = 1
    max_clarifications: int = 2


class MemoryState(BaseModel):
    """Short-term window + rolling summary + durable facts."""

    summary: str | None = None
    recent_window: list[dict[str, Any]] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    covered_through_turn: int = 0


class RetrievalState(BaseModel):
    """Query planning inputs/outputs and the retrieved evidence set."""

    query: str | None = None
    expanded_queries: list[str] = Field(default_factory=list)
    namespace: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    candidates: list[RetrievedChunk] = Field(default_factory=list)
    context: str | None = None
    max_relevance_score: float = 0.0
    sufficient: bool = False


class ApprovalState(BaseModel):
    """Human-in-the-loop / handoff status."""

    requires_approval: bool = False
    approved: bool | None = None
    reviewer_id: str | None = None
    awaiting_human: bool = False
    handoff_queue: str | None = None
    decision_notes: str | None = None


class StreamingState(BaseModel):
    """Streaming egress flags for the responder."""

    enabled: bool = False
    typing: bool = False
    cancelled: bool = False
    tokens_emitted: int = 0


# --------------------------------------------------------------------------- #
# The graph state
# --------------------------------------------------------------------------- #
class AgentState(TypedDict, total=False):
    """The LangGraph state object (single shared contract)."""

    # identity / correlation
    thread_id: str
    org_id: str
    user_id: str
    trace_id: str
    turn_id: int

    # append-reduced channels
    messages: Annotated[list[dict[str, Any]], merge_lists]
    node_path: Annotated[list[str], merge_lists]
    audit_trail: Annotated[list[dict[str, Any]], merge_lists]

    # grouped sub-states
    conversation: ConversationState
    execution: ExecutionContext
    memory: MemoryState
    retrieval: RetrievalState
    approval: ApprovalState
    streaming: StreamingState

    # hot routing / output fields (last-write-wins)
    raw_user_message: str
    normalized_query: str
    redacted_query: str
    query_hash: str
    cache_hit: bool
    cached_answer: str | None
    safety_verdict: str | None
    injection_flag: bool

    decision: Decision | None
    final_confidence: float | None
    grounding_score: float | None
    contradiction_flag: bool
    retry_count: int
    clarification_rounds: int

    draft_answer: str | None
    claims: list[str]
    citations: list[Citation]
    response_text: str | None
    abstained: bool
    general_answer: bool  # answer came from general LLM knowledge, not grounded KB
    quick_replies: list[str]  # suggested clickable replies for a clarification turn

    # L1/L2/L3 support tiering (which tier resolved the turn)
    tier: str  # "L1" self-service | "L2" assisted resolution | "L3" human handoff
    l2_attempted: bool  # the L2 resolver has run this turn
    l2_resolved: bool  # the L2 resolver produced a deliverable answer

    ticket_id: str | None
    escalation_reason: str | None

    error: str | None


def initial_state(
    *,
    thread_id: str,
    org_id: str,
    user_id: str,
    trace_id: str,
    turn_id: int,
    user_message: str,
    streaming: bool = False,
    retry_budget: int = 1,
    max_clarifications: int = 2,
    clarification_rounds: int = 0,
) -> AgentState:
    """Build a fresh ``AgentState`` seeded from an incoming user turn."""
    return AgentState(
        thread_id=thread_id,
        org_id=org_id,
        user_id=user_id,
        trace_id=trace_id,
        turn_id=turn_id,
        messages=[{"role": "user", "content": user_message}],
        node_path=[],
        audit_trail=[],
        conversation=ConversationState(),
        execution=ExecutionContext(
            trace_id=trace_id,
            turn_id=turn_id,
            retry_budget=retry_budget,
            max_clarifications=max_clarifications,
        ),
        memory=MemoryState(),
        retrieval=RetrievalState(),
        approval=ApprovalState(),
        streaming=StreamingState(enabled=streaming),
        raw_user_message=user_message,
        normalized_query=user_message,
        redacted_query=user_message,
        query_hash="",
        cache_hit=False,
        cached_answer=None,
        safety_verdict=None,
        injection_flag=False,
        decision=None,
        final_confidence=None,
        grounding_score=None,
        contradiction_flag=False,
        retry_count=0,
        clarification_rounds=clarification_rounds,
        draft_answer=None,
        claims=[],
        citations=[],
        response_text=None,
        abstained=False,
        general_answer=False,
        quick_replies=[],
        tier="L1",
        l2_attempted=False,
        l2_resolved=False,
        ticket_id=None,
        escalation_reason=None,
        error=None,
    )


__all__ = [
    "merge_lists",
    "RetrievedChunk",
    "Citation",
    "ConversationState",
    "ExecutionContext",
    "MemoryState",
    "RetrievalState",
    "ApprovalState",
    "StreamingState",
    "AgentState",
    "initial_state",
]
