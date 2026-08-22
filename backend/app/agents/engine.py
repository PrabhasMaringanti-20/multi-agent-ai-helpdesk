"""HelpdeskAIEngine — compile the graph once; run/stream turns (Phase 10/11).

Streaming policy: tokens are streamed AFTER the reliability gates decide, so the
user never sees ungrounded text. The engine runs the graph to a decision, then
emits a typing indicator, the final answer token-by-token (honoring a
``CancellationToken``), citations, the decision, and a done event. Reconnect is
achieved by resuming on the same ``thread_id`` via the checkpointer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.agents.checkpointer import build_memory_checkpointer
from app.agents.config_schema import GraphDeps, build_config
from app.agents.graph import compile_graph
from app.agents.state import AgentState, initial_state
from app.agents.streaming import (
    CancellationToken,
    StreamEvent,
    cancelled_event,
    citations_event,
    decision_event,
    done_event,
    error_event,
    quick_replies_event,
    token_event,
    typing_event,
)
from app.core.logging import get_logger

_logger = get_logger(__name__)


class HelpdeskAIEngine:
    def __init__(self, *, checkpointer: Any | None = None, recursion_limit: int = 40) -> None:
        self._compiled = compile_graph(checkpointer or build_memory_checkpointer())
        self._recursion_limit = recursion_limit

    def _config(self, deps: GraphDeps, thread_id: str) -> dict[str, Any]:
        config = build_config(deps, thread_id=thread_id)
        config["recursion_limit"] = self._recursion_limit
        return config

    async def run(
        self,
        *,
        deps: GraphDeps,
        thread_id: str,
        org_id: str,
        user_id: str,
        trace_id: str,
        turn_id: int,
        user_message: str,
        streaming: bool = False,
        clarification_rounds: int = 0,
    ) -> AgentState:
        seed = initial_state(
            thread_id=thread_id,
            org_id=org_id,
            user_id=user_id,
            trace_id=trace_id,
            turn_id=turn_id,
            user_message=user_message,
            streaming=streaming,
            retry_budget=deps.thresholds.for_category(None).retry_budget,
            clarification_rounds=clarification_rounds,
        )
        return await self._compiled.ainvoke(seed, self._config(deps, thread_id))

    async def astream(
        self,
        *,
        deps: GraphDeps,
        thread_id: str,
        org_id: str,
        user_id: str,
        trace_id: str,
        turn_id: int,
        user_message: str,
        cancel_token: CancellationToken | None = None,
        clarification_rounds: int = 0,
    ) -> AsyncIterator[StreamEvent]:
        yield typing_event(True)
        try:
            final = await self.run(
                deps=deps,
                thread_id=thread_id,
                org_id=org_id,
                user_id=user_id,
                trace_id=trace_id,
                turn_id=turn_id,
                user_message=user_message,
                streaming=True,
                clarification_rounds=clarification_rounds,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a stream error event
            _logger.exception("Graph run failed: %s", exc)
            yield error_event(str(exc))
            return

        yield typing_event(False)
        text = final.get("response_text") or ""
        for index, word in enumerate(text.split(" ")):
            if cancel_token is not None and cancel_token.cancelled:
                yield cancelled_event()
                return
            yield token_event(word + " ", index)

        citations = final.get("citations") or []
        if citations:
            yield citations_event([c.model_dump() for c in citations])
        quick = final.get("quick_replies") or []
        if quick:
            yield quick_replies_event(quick)
        yield decision_event(
            str(final.get("decision")), final.get("final_confidence"), tier=final.get("tier")
        )
        yield done_event(text, str(final.get("decision")))


_engine: HelpdeskAIEngine | None = None


def get_ai_engine() -> HelpdeskAIEngine:
    global _engine
    if _engine is None:
        _engine = HelpdeskAIEngine()
    return _engine


__all__ = ["HelpdeskAIEngine", "get_ai_engine"]
