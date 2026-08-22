"""Chat route: streaming AI turn over SSE (ARCHITECTURE.md §5.3 / §9.4).

``POST /chat/messages`` runs the LangGraph engine and streams typed SSE events
(typing, tokens, citations, decision, done). The DB session is created INSIDE
the stream generator (not request-scoped) because a request-scoped session would
close when the ``StreamingResponse`` object is returned, before the body streams.
User + assistant turns are persisted so the conversation history endpoints work.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import AiEngineDep, CurrentPrincipal, get_graph_deps
from app.core.constants import ConversationStatus, Decision, MessageRole
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.chat import ChatTurnRequest

router = APIRouter(prefix="/chat", tags=["chat"])
_logger = get_logger(__name__)


def _parse_decision(value: str | None) -> Decision | None:
    try:
        return Decision(value) if value else None
    except ValueError:
        return None


@router.post("/messages", summary="Submit a chat turn and stream the response (SSE)")
async def send_message(
    payload: ChatTurnRequest,
    request: Request,
    principal: CurrentPrincipal,
    engine: AiEngineDep,
) -> StreamingResponse:
    trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4().hex
    org_id = str(principal.org_id)
    user_id = str(principal.user_id)
    message = payload.message
    try:
        conv_uuid = uuid.UUID(payload.thread_id) if payload.thread_id else uuid.uuid4()
    except ValueError:
        conv_uuid = uuid.uuid4()

    async def event_stream():
        async with SessionFactory() as session:
            deps = get_graph_deps(session)
            conversations = ConversationRepository(session)

            conversation = await conversations.get(conv_uuid)
            if conversation is None:
                conversation = await conversations.create(
                    id=conv_uuid,
                    org_id=principal.org_id,
                    user_id=principal.user_id,
                    status=ConversationStatus.ACTIVE,
                )
            turn_id = await conversations.next_turn_id(conversation.id)
            # Carry clarification count across turns so unresolved issues escalate
            # to a ticket instead of re-asking forever.
            prior_clarifications = await conversations.count_assistant_clarifications(
                conversation.id
            )
            await conversations.add_message(
                conversation_id=conversation.id,
                turn_id=turn_id,
                role=MessageRole.USER,
                content=message,
                trace_id=trace_id,
            )
            await session.commit()

            final_text = ""
            final_citations: list = []
            final_decision: str | None = None
            try:
                async for event in engine.astream(
                    deps=deps,
                    thread_id=str(conversation.id),
                    org_id=org_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    turn_id=turn_id,
                    user_message=message,
                    clarification_rounds=prior_clarifications,
                ):
                    if event.type.value == "done":
                        final_text = str(event.data.get("response_text", ""))
                    elif event.type.value == "citations":
                        final_citations = event.data.get("citations", []) or []
                    elif event.type.value == "decision":
                        final_decision = event.data.get("decision")
                    yield event.to_sse()
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error frame
                _logger.exception("Chat stream failed: %s", exc)
                yield 'event: error\ndata: {"type": "error", "data": {"message": "internal error"}}\n\n'
                return

            if final_text:
                await conversations.add_message(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    role=MessageRole.ASSISTANT,
                    content=final_text,
                    trace_id=trace_id,
                    citations=final_citations or None,
                    decision=_parse_decision(final_decision),
                )
                await conversations.touch_last_message(conversation)
                await session.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
