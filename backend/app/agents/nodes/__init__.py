"""The canonical agent nodes (one file per node, per ARCHITECTURE.md §4).

Mapping to the Phase-6 functional node list:
  Router            -> intent_classifier (+ ingress_guard routing)
  Retriever         -> query_planner + rag_retriever
  Knowledge Answer  -> solution_synthesizer
  Confidence Eval   -> grounding_verifier + confidence_gate
  Clarifier         -> info_collector
  Memory Update     -> memory_manager
  Ticket Creator    -> ticket_creator
  Escalation        -> human_handoff
  Response Generator-> responder
  Feedback / Audit  -> handled by responder (analytics/audit) + the learning subgraph
"""

from app.agents.nodes.confidence_gate import confidence_gate
from app.agents.nodes.grounding_verifier import grounding_verifier
from app.agents.nodes.human_handoff import human_handoff
from app.agents.nodes.info_collector import info_collector
from app.agents.nodes.ingress_guard import ingress_guard
from app.agents.nodes.intent_classifier import intent_classifier
from app.agents.nodes.l2_resolver import l2_resolver
from app.agents.nodes.memory_manager import memory_manager
from app.agents.nodes.query_planner import query_planner
from app.agents.nodes.rag_retriever import rag_retriever
from app.agents.nodes.responder import responder
from app.agents.nodes.retrieval_gate import retrieval_gate
from app.agents.nodes.solution_synthesizer import solution_synthesizer
from app.agents.nodes.ticket_creator import ticket_creator

NODES = {
    "ingress_guard": ingress_guard,
    "memory_manager": memory_manager,
    "intent_classifier": intent_classifier,
    "query_planner": query_planner,
    "rag_retriever": rag_retriever,
    "retrieval_gate": retrieval_gate,
    "solution_synthesizer": solution_synthesizer,
    "grounding_verifier": grounding_verifier,
    "confidence_gate": confidence_gate,
    "info_collector": info_collector,
    "l2_resolver": l2_resolver,
    "ticket_creator": ticket_creator,
    "human_handoff": human_handoff,
    "responder": responder,
}

__all__ = ["NODES", *sorted(NODES)]
