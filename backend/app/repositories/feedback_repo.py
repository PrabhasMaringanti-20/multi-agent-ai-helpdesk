"""Data access for feedback and relevance signals."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.feedback import Feedback, RelevanceSignal
from app.repositories.base import BaseRepository


class FeedbackRepository(BaseRepository[Feedback]):
    model = Feedback

    async def list_unprocessed(self, *, limit: int = 100) -> Sequence[Feedback]:
        stmt = (
            select(Feedback)
            .where(Feedback.processed_at.is_(None))
            .order_by(Feedback.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def mark_processed(self, feedback: Feedback) -> Feedback:
        feedback.processed_at = datetime.now(UTC)
        await self.session.flush()
        return feedback

    async def get_signal(
        self, doc_id: uuid.UUID, chunk_id: uuid.UUID | None
    ) -> RelevanceSignal | None:
        stmt = select(RelevanceSignal).where(RelevanceSignal.doc_id == doc_id)
        if chunk_id is None:
            stmt = stmt.where(RelevanceSignal.chunk_id.is_(None))
        else:
            stmt = stmt.where(RelevanceSignal.chunk_id == chunk_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none()

    async def upsert_signal(
        self,
        *,
        doc_id: uuid.UUID,
        chunk_id: uuid.UUID | None = None,
        upvote_delta: int = 0,
        downvote_delta: int = 0,
        impression_delta: int = 0,
        resolution_success_delta: int = 0,
    ) -> RelevanceSignal:
        signal = await self.get_signal(doc_id, chunk_id)
        if signal is None:
            signal = RelevanceSignal(doc_id=doc_id, chunk_id=chunk_id)
            self.session.add(signal)
        signal.upvotes += upvote_delta
        signal.downvotes += downvote_delta
        signal.impressions += impression_delta
        signal.resolution_success += resolution_success_delta
        await self.session.flush()
        return signal


__all__ = ["FeedbackRepository"]
