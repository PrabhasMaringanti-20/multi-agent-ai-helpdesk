"""Deterministic routers + conditional-edge selectors (ARCHITECTURE.md §1.2/§1.3).

Two deterministic decision functions (retrieval gate, confidence gate) plus the
edge-selector functions LangGraph uses for conditional routing. Selectors only
READ fields that the nodes have already written to ``AgentState`` — they contain
no side effects. The graph always terminates in deliver or handoff; loop budgets
force escalation when exceeded.
"""

from __future__ import annotations

from app.agents.state import AgentState
from app.core.constants import Decision
from app.registries.threshold_registry import ThresholdSet


# --------------------------------------------------------------------------- #
# Deterministic gate decisions (used by the gate nodes)
# --------------------------------------------------------------------------- #
def retrieval_is_sufficient(state: AgentState, thresholds: ThresholdSet) -> bool:
    retrieval = state["retrieval"]
    return bool(retrieval.candidates) and retrieval.max_relevance_score >= thresholds.retrieval


def decide_confidence(
    state: AgentState,
    thresholds: ThresholdSet,
    *,
    final_confidence: float,
    grounding_score: float,
    contradiction: bool,
    citation_valid: bool,
    answer_relevant: bool,
) -> Decision:
    """Central policy router (§1.3). Order matters — hallucination guard first."""
    conversation = state["conversation"]
    execution = state["execution"]

    # (0) general (non-grounded) LLM answer: deliver it if we have a real answer.
    # These have no KB citations by design, so they bypass the grounding guard.
    if state.get("general_answer"):
        return Decision.DELIVER if answer_relevant else Decision.ESCALATE

    # (a) hard hallucination guard — overrides any self-confidence
    if contradiction or not citation_valid or not answer_relevant:
        return Decision.ESCALATE
    # (b) confident + grounded -> deliver
    if final_confidence >= thresholds.deliver and grounding_score >= thresholds.grounding_min:
        return Decision.DELIVER
    # (c) borderline + resolvable via clarification
    if conversation.missing_slots and state["clarification_rounds"] < execution.max_clarifications:
        return Decision.CLARIFY
    # (d) thin retrieval, retry within budget
    if state["retry_count"] < thresholds.retry_budget:
        return Decision.RETRY_RETRIEVAL
    # (e) otherwise escalate
    return Decision.ESCALATE


# --------------------------------------------------------------------------- #
# Conditional-edge selectors (used by graph.add_conditional_edges)
# --------------------------------------------------------------------------- #
def route_after_ingress(state: AgentState) -> str:
    if state.get("safety_verdict") == "block" or state.get("cache_hit"):
        return "responder"
    control = state["conversation"].control_intent
    if control in ("greeting", "cancel"):
        return "responder"
    if control == "human_request":
        return "ticket_creator"
    return "memory_manager"


def route_after_memory(state: AgentState) -> str:
    """memory_manager is dual-mode: load (-> classify) then persist (-> END)."""
    return "end" if state.get("response_text") is not None else "continue"


def route_after_intent(state: AgentState) -> str:
    control = state["conversation"].control_intent
    if control == "smalltalk":
        return "responder"
    if control == "out_of_scope":
        return "ticket_creator"
    # Answer-first: always retrieve (KB + uploaded files) before asking anything.
    # If retrieval is thin AND required info is missing, the retrieval gate routes
    # to the clarifier (with quick replies, once); otherwise we answer directly.
    return "query_planner"


def route_after_retrieval_gate(state: AgentState) -> str:
    retrieval = state["retrieval"]
    if retrieval.sufficient:
        return "solution_synthesizer"
    # Retrieval found nothing: ask for missing info ONCE (guided quick replies),
    # then give a best-effort general answer rather than looping or escalating.
    if state["conversation"].missing_slots and state["clarification_rounds"] == 0:
        return "info_collector"
    return "solution_synthesizer"


def route_after_synthesizer(state: AgentState) -> str:
    return "ticket_creator" if state.get("abstained") else "grounding_verifier"


def route_after_confidence(state: AgentState) -> str:
    decision = state.get("decision")
    if decision == Decision.DELIVER:
        return "responder"
    if decision == Decision.CLARIFY:
        return "info_collector"
    if decision == Decision.RETRY_RETRIEVAL:
        return "query_planner"
    return "ticket_creator"


def route_after_info_collector(state: AgentState) -> str:
    conversation = state["conversation"]
    execution = state["execution"]
    if not conversation.missing_slots:
        return "rag_retriever"
    if state["clarification_rounds"] >= execution.max_clarifications:
        return "ticket_creator"
    return "responder"  # ask the user; turn ends (interrupt) awaiting reply


def route_after_l2(state: AgentState) -> str:
    """Deliver the L2 answer, or escalate to L3 (human) if L2 could not resolve."""
    return "responder" if state.get("l2_resolved") else "ticket_creator"


__all__ = [
    "retrieval_is_sufficient",
    "decide_confidence",
    "route_after_ingress",
    "route_after_memory",
    "route_after_intent",
    "route_after_retrieval_gate",
    "route_after_synthesizer",
    "route_after_confidence",
    "route_after_info_collector",
    "route_after_l2",
]
