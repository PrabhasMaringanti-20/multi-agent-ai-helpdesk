"""Conversation history routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, PaginationDep, SessionDep
from app.core.exceptions import NotFoundError
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.common import Page, build_page
from app.schemas.conversation import ConversationResponse, MessageDTO

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", summary="List the current user's conversations")
async def list_conversations(
    principal: CurrentPrincipal, session: SessionDep, pagination: PaginationDep
) -> Page[ConversationResponse]:
    repo = ConversationRepository(session)
    rows = await repo.list_for_user(
        principal.org_id, principal.user_id, limit=pagination.limit, offset=pagination.offset
    )
    total = await repo.count_for_org(principal.org_id, user_id=principal.user_id, deleted_at=None)
    return build_page([ConversationResponse.model_validate(r) for r in rows], total, pagination)


@router.get("/{conversation_id}/messages", summary="Get a conversation transcript")
async def get_messages(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> list[MessageDTO]:
    repo = ConversationRepository(session)
    conversation = await repo.get_for_user(conversation_id, principal.user_id)
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    messages = await repo.list_messages(conversation_id)
    return [MessageDTO.model_validate(m) for m in messages]


__all__ = ["router"]
