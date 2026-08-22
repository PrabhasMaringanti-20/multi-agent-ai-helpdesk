"""Main chat subgraph assembly (ARCHITECTURE.md §1.2).

Wires the 13 canonical nodes with the frozen conditional edges. The graph always
terminates in deliver (responder) or handoff (ticket_creator -> human_handoff ->
responder); retry/clarification budgets force escalation, so there are no
unbounded loops. Every node is reachable from START and every path reaches END
via ``responder -> memory_manager(persist) -> END``.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents import routing
from app.agents.nodes import (
    confidence_gate,
    grounding_verifier,
    human_handoff,
    info_collector,
    ingress_guard,
    intent_classifier,
    l2_resolver,
    memory_manager,
    query_planner,
    rag_retriever,
    responder,
    retrieval_gate,
    solution_synthesizer,
    ticket_creator,
)
from app.agents.state import AgentState


def build_graph() -> StateGraph:
    """Construct (but do not compile) the main chat StateGraph."""
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("ingress_guard", ingress_guard)
    graph.add_node("memory_manager", memory_manager)
    graph.add_node("intent_classifier", intent_classifier)
    graph.add_node("query_planner", query_planner)
    graph.add_node("rag_retriever", rag_retriever)
    graph.add_node("retrieval_gate", retrieval_gate)
    graph.add_node("solution_synthesizer", solution_synthesizer)
    graph.add_node("grounding_verifier", grounding_verifier)
    graph.add_node("confidence_gate", confidence_gate)
    graph.add_node("info_collector", info_collector)
    graph.add_node("l2_resolver", l2_resolver)
    graph.add_node("ticket_creator", ticket_creator)
    graph.add_node("human_handoff", human_handoff)
    graph.add_node("responder", responder)

    graph.add_edge(START, "ingress_guard")
    graph.add_conditional_edges(
        "ingress_guard",
        routing.route_after_ingress,
        {
            "responder": "responder",
            "ticket_creator": "ticket_creator",
            "memory_manager": "memory_manager",
        },
    )
    graph.add_conditional_edges(
        "memory_manager",
        routing.route_after_memory,
        {"continue": "intent_classifier", "end": END},
    )
    graph.add_conditional_edges(
        "intent_classifier",
        routing.route_after_intent,
        {
            "responder": "responder",
            "ticket_creator": "ticket_creator",
            "info_collector": "info_collector",
            "query_planner": "query_planner",
        },
    )
    graph.add_edge("query_planner", "rag_retriever")
    graph.add_edge("rag_retriever", "retrieval_gate")
    graph.add_conditional_edges(
        "retrieval_gate",
        routing.route_after_retrieval_gate,
        {
            "solution_synthesizer": "solution_synthesizer",
            "info_collector": "info_collector",
            # Soft escalation: try the L2 assisted-resolution tier before a human.
            "ticket_creator": "l2_resolver",
        },
    )
    graph.add_conditional_edges(
        "solution_synthesizer",
        routing.route_after_synthesizer,
        {"ticket_creator": "l2_resolver", "grounding_verifier": "grounding_verifier"},
    )
    graph.add_edge("grounding_verifier", "confidence_gate")
    graph.add_conditional_edges(
        "confidence_gate",
        routing.route_after_confidence,
        {
            "responder": "responder",
            "info_collector": "info_collector",
            "query_planner": "query_planner",
            # Soft escalation: try L2 before handing off to a human.
            "ticket_creator": "l2_resolver",
        },
    )
    graph.add_conditional_edges(
        "l2_resolver",
        routing.route_after_l2,
        {"responder": "responder", "ticket_creator": "ticket_creator"},
    )
    graph.add_conditional_edges(
        "info_collector",
        routing.route_after_info_collector,
        {
            "rag_retriever": "rag_retriever",
            "ticket_creator": "ticket_creator",
            "responder": "responder",
        },
    )
    graph.add_edge("ticket_creator", "human_handoff")
    graph.add_edge("human_handoff", "responder")
    graph.add_edge("responder", "memory_manager")

    return graph


def compile_graph(checkpointer: Any | None = None) -> Any:
    """Compile the main graph (optionally with a checkpointer for durable threads)."""
    return build_graph().compile(checkpointer=checkpointer)


__all__ = ["build_graph", "compile_graph"]
