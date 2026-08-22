"""Data access for conversation memory: rolling summaries and durable facts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update

from app.models.conversation import ConversationSummary, MemoryFact
from app.repositories.base import BaseRepository


class MemoryRepository(BaseRepository[MemoryFact]):
    model = MemoryFact

    # ---- durable facts -------------------------------------------------- #
    async def list_facts(self, user_id: uuid.UUID) -> Sequence[MemoryFact]:
        stmt = select(MemoryFact).where(MemoryFact.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_fact(self, user_id: uuid.UUID, fact_key: str) -> MemoryFact | None:
        return await self.get_by(user_id=user_id, fact_key=fact_key)

    async def upsert_fact(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        fact_key: str,
        fact_value: str,
        confidence: float = 1.0,
        source_conversation_id: uuid.UUID | None = None,
    ) -> MemoryFact:
        existing = await self.get_fact(user_id, fact_key)
        if existing is not None:
            existing.fact_value = fact_value
            existing.confidence = confidence
            if source_conversation_id is not None:
                existing.source_conversation_id = source_conversation_id
            await self.session.flush()
            return existing
        fact = MemoryFact(
            org_id=org_id,
            user_id=user_id,
            fact_key=fact_key,
            fact_value=fact_value,
            confidence=confidence,
            source_conversation_id=source_conversation_id,
        )
        self.session.add(fact)
        await self.session.flush()
        return fact

    # ---- rolling summaries --------------------------------------------- #
    async def get_current_summary(self, conversation_id: uuid.UUID) -> ConversationSummary | None:
        stmt = (
            select(ConversationSummary)
            .where(
                ConversationSummary.conversation_id == conversation_id,
                ConversationSummary.is_current.is_(True),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_summary(
        self,
        *,
        conversation_id: uuid.UUID,
        summary_text: str,
        covered_through_turn: int,
    ) -> ConversationSummary:
        """Retire the current summary and insert a new current one (versioned)."""
        current = await self.get_current_summary(conversation_id)
        next_version = 1
        if current is not None:
            next_version = current.version + 1
            await self.session.execute(
                update(ConversationSummary)
                .where(
                    ConversationSummary.conversation_id == conversation_id,
                    ConversationSummary.is_current.is_(True),
                )
                .values(is_current=False)
            )
        summary = ConversationSummary(
            conversation_id=conversation_id,
            summary_text=summary_text,
            covered_through_turn=covered_through_turn,
            version=next_version,
            is_current=True,
        )
        self.session.add(summary)
        await self.session.flush()
        return summary


__all__ = ["MemoryRepository"]
