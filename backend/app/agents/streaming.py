"""Streaming primitives (Phase 10): stream events + a cancellation token.

The engine emits a typed event stream (typing indicator, tokens, partial text,
citations, decision, done/error/cancelled). Reconnect is achieved by resuming
the graph on the same ``thread_id`` from the checkpointer; these types describe
the wire events the transport (SSE) serializes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StreamEventType(StrEnum):
    TYPING = "typing"
    TOKEN = "token"
    PARTIAL = "partial"
    CITATIONS = "citations"
    QUICK_REPLIES = "quick_replies"
    DECISION = "decision"
    TICKET = "ticket"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)
    index: int | None = None

    def to_sse(self) -> str:
        import json

        payload = {"type": self.type.value, "data": self.data}
        if self.index is not None:
            payload["index"] = self.index
        return f"event: {self.type.value}\ndata: {json.dumps(payload)}\n\n"


def typing_event(on: bool = True) -> StreamEvent:
    return StreamEvent(StreamEventType.TYPING, {"typing": on})


def token_event(text: str, index: int) -> StreamEvent:
    return StreamEvent(StreamEventType.TOKEN, {"text": text}, index=index)


def citations_event(citations: list[dict[str, Any]]) -> StreamEvent:
    return StreamEvent(StreamEventType.CITATIONS, {"citations": citations})


def quick_replies_event(options: list[str]) -> StreamEvent:
    return StreamEvent(StreamEventType.QUICK_REPLIES, {"options": options})


def decision_event(decision: str, confidence: float | None, tier: str | None = None) -> StreamEvent:
    return StreamEvent(
        StreamEventType.DECISION,
        {"decision": decision, "confidence": confidence, "tier": tier},
    )


def done_event(response_text: str, decision: str) -> StreamEvent:
    return StreamEvent(StreamEventType.DONE, {"response_text": response_text, "decision": decision})


def error_event(message: str) -> StreamEvent:
    return StreamEvent(StreamEventType.ERROR, {"message": message})


def cancelled_event() -> StreamEvent:
    return StreamEvent(StreamEventType.CANCELLED, {})


class CancellationToken:
    """Cooperative cancellation shared with a running stream."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


__all__ = [
    "StreamEventType",
    "StreamEvent",
    "typing_event",
    "token_event",
    "citations_event",
    "quick_replies_event",
    "decision_event",
    "done_event",
    "error_event",
    "cancelled_event",
    "CancellationToken",
]
