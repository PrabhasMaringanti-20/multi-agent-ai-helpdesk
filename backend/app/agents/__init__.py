"""LangGraph multi-agent orchestrator package (the AI engine).

Modules (per ARCHITECTURE.md §4): ``state`` (AgentState contract), ``routing``
(deterministic gates), ``confidence`` (scoring), ``config_schema`` (injected
dependency contract), ``checkpointer`` (Postgres checkpointer), ``graph`` /
``learning_graph`` (subgraphs), ``nodes/*`` (the canonical agent nodes),
``streaming`` (stream events), ``tools`` (tool calling), and ``engine`` (facade).
"""
