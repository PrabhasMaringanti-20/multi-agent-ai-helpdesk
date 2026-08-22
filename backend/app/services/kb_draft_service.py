"""Auto-draft a knowledge-base article from a resolved ticket (the learning loop).

When a ticket is resolved, an LLM drafts a reusable KB article from the problem +
the engineer's resolution and files it as ``PENDING_REVIEW`` (linked to the ticket
via ``origin_ticket_id``). An SME then approves/publishes it through the existing
KB flow, at which point it becomes dense-searchable. Idempotent per ticket: a
second call returns the existing draft instead of creating a duplicate.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select

from app.core.constants import DocStatus, SourceType, TicketEventType
from app.models.knowledge import KbDocument
from app.providers.base import ChatMessage
from app.registries.category_registry import get_category_registry
from app.repositories.kb_repo import KnowledgeRepository
from app.repositories.ticket_repo import TicketRepository


class KbDraftService:
    def __init__(
        self,
        session: Any,
        llm: Any,
        kb_repo: KnowledgeRepository,
        ticket_repo: TicketRepository,
    ) -> None:
        self.session = session
        self.llm = llm
        self.kb = kb_repo
        self.tickets = ticket_repo

    async def _existing_draft(self, ticket_id: uuid.UUID) -> KbDocument | None:
        stmt = select(KbDocument).where(KbDocument.origin_ticket_id == ticket_id).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _resolution_text(self, ticket_id: uuid.UUID) -> str:
        """Collect the engineer's side of the ticket thread as the resolution."""
        lines: list[str] = []
        for ev in await self.tickets.list_events(ticket_id):
            if ev.event_type == TicketEventType.COMMENTED:
                payload = ev.payload or {}
                if payload.get("sender_role") == "engineer" and payload.get("text"):
                    lines.append(str(payload["text"]).strip())
        return "\n".join(lines)

    async def _draft_body(self, subject: str, category: str, resolution: str) -> str:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a senior IT support engineer writing a concise INTERNAL "
                    "knowledge-base article from a resolved ticket. Output GitHub-flavored "
                    "markdown with these sections: a single '# ' title line, then "
                    "'## Problem', '## Symptoms', '## Resolution Steps' (a numbered list), "
                    "and '## Notes'. Base it ONLY on the ticket details and the engineer's "
                    "resolution below — do not invent specifics, credentials, or timelines."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Ticket subject: {subject}\nCategory: {category}\n\n"
                    f"Engineer resolution notes:\n{resolution or '(no explicit engineer notes)'}\n\n"
                    "Write the knowledge-base article:"
                ),
            ),
        ]
        result = await self.llm.generate(messages)
        return result.text.strip()

    async def draft_from_ticket(self, ticket: Any, principal: Any) -> tuple[KbDocument, bool]:
        """Return (document, created). ``created`` is False when a draft already existed."""
        existing = await self._existing_draft(ticket.id)
        if existing is not None:
            return existing, False

        registry = get_category_registry()
        category = ticket.category if ticket.category in registry else "application_error"
        namespace = registry.get(category).retrieval_namespace
        resolution = await self._resolution_text(ticket.id)
        body = await self._draft_body(ticket.subject, category, resolution)
        title = ticket.subject.strip()[:300] or "Resolved ticket"

        doc = await self.kb.create(
            org_id=ticket.org_id,
            title=title,
            source_type=SourceType.ENGINEER_RESOLUTION,
            category=category,
            retrieval_namespace=namespace,
            doc_status=DocStatus.PENDING_REVIEW,
            version=1,
            checksum=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            created_by_user_id=principal.user_id,
            source_uri=f"ticket://{ticket.id}",
            origin_ticket_id=ticket.id,
        )
        await self.session.flush()
        await self.kb.add_chunk(
            chunk_id=uuid.uuid4(),
            doc_id=doc.id,
            org_id=ticket.org_id,
            category_key=category,
            retrieval_namespace=namespace,
            chunk_index=0,
            text=body,
            embedding_model_id="pending",
            doc_status=DocStatus.PENDING_REVIEW,
            version=1,
            token_count=len(body.split()),
            source_uri=doc.source_uri,
        )
        await self.kb.add_version(
            doc_id=doc.id,
            version=1,
            title=title,
            doc_status=DocStatus.PENDING_REVIEW,
            checksum=doc.checksum,
            source_uri=doc.source_uri,
            change_summary=f"Auto-drafted from resolved ticket {ticket.id}",
            created_by_user_id=principal.user_id,
        )
        await self.session.commit()
        return doc, True


__all__ = ["KbDraftService"]
