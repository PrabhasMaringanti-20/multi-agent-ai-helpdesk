"""Data access for conversations and messages."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.core.constants import Decision, MessageRole
from app.models.conversation import Conversation, Message
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def list_for_user(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Conversation]:
        stmt = (
            select(Conversation)
            .where(
                Conversation.org_id == org_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.last_message_at.desc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_for_user(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def touch_last_message(
        self, conversation: Conversation, when: datetime | None = None
    ) -> Conversation:
        conversation.last_message_at = when or datetime.now(UTC)
        await self.session.flush()
        return conversation

    # ---- messages ------------------------------------------------------- #
    async def next_turn_id(self, conversation_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Message.turn_id), 0)).where(
            Message.conversation_id == conversation_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    async def add_message(
        self,
        *,
        conversation_id: uuid.UUID,
        turn_id: int,
        role: MessageRole,
        content: str,
        trace_id: str,
        citations: list[dict[str, Any]] | None = None,
        decision: Any | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            turn_id=turn_id,
            role=role,
            content=content,
            trace_id=trace_id,
            citations=citations,
            decision=decision,
            token_usage=token_usage,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def count_assistant_clarifications(self, conversation_id: uuid.UUID) -> int:
        """How many times the assistant has already asked for clarification here."""
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.ASSISTANT,
                Message.decision == Decision.CLARIFY,
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.turn_id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


__all__ = ["ConversationRepository"]
