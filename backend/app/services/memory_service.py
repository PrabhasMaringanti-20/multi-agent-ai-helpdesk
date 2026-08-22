"""Conversation memory engine (ARCHITECTURE.md §4 services.memory_service).

Combines the three memory tiers from the design:
- short-term: a rolling recent-message window from ``messages``,
- long-term: a rolling LLM summary persisted in ``conversation_summaries``,
- durable: per-user facts in ``memory_facts``.

Summarization (compression) triggers once a conversation exceeds a turn
threshold, keeping token cost flat regardless of length. Reuses the existing
``MemoryRepository`` / ``ConversationRepository`` and an injected LLM provider.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.agents.state import MemoryState
from app.core.logging import get_logger
from app.providers.base import LLMProvider
from app.registries.prompt_registry import PromptRegistry, get_prompt_registry
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import MemoryRepository

_logger = get_logger(__name__)


class _FactsResult(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict)


class MemoryService:
    def __init__(
        self,
        memory_repo: MemoryRepository,
        conversation_repo: ConversationRepository,
        llm: LLMProvider,
        prompts: PromptRegistry | None = None,
        *,
        window_turns: int = 10,
        summary_trigger_turns: int = 12,
    ) -> None:
        self._memory = memory_repo
        self._conversations = conversation_repo
        self._llm = llm
        self._prompts = prompts or get_prompt_registry()
        self._window_turns = window_turns
        self._summary_trigger_turns = summary_trigger_turns

    # ------------------------------------------------------------------ #
    # Load
    # ------------------------------------------------------------------ #
    async def load_state(self, *, user_id: uuid.UUID, conversation_id: uuid.UUID) -> MemoryState:
        messages = await self._conversations.list_messages(conversation_id, limit=200)
        window = [{"role": m.role, "content": m.content} for m in messages[-self._window_turns :]]
        summary_row = await self._memory.get_current_summary(conversation_id)
        facts = {f.fact_key: f.fact_value for f in await self._memory.list_facts(user_id)}
        return MemoryState(
            summary=summary_row.summary_text if summary_row else None,
            recent_window=window,
            facts=facts,
            covered_through_turn=summary_row.covered_through_turn if summary_row else 0,
        )

    async def save_fact(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        fact_key: str,
        fact_value: str,
        conversation_id: uuid.UUID | None = None,
    ) -> None:
        """Explicitly persist a durable fact (used by the ``save_memory`` tool)."""
        await self._memory.upsert_fact(
            org_id=org_id,
            user_id=user_id,
            fact_key=fact_key,
            fact_value=fact_value,
            source_conversation_id=conversation_id,
        )

    # ------------------------------------------------------------------ #
    # Update / compress
    # ------------------------------------------------------------------ #
    async def persist_turn(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        turn_id: int,
        state: MemoryState,
    ) -> MemoryState:
        """Post-response memory maintenance: summarize + extract durable facts."""
        updated = state
        if turn_id >= self._summary_trigger_turns and turn_id > state.covered_through_turn:
            updated = await self._summarize(
                conversation_id=conversation_id,
                turn_id=turn_id,
                prior_summary=state.summary,
                window=state.recent_window,
            )
        await self._extract_and_store_facts(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            window=state.recent_window,
            into=updated,
        )
        return updated

    async def _summarize(
        self,
        *,
        conversation_id: uuid.UUID,
        turn_id: int,
        prior_summary: str | None,
        window: list[dict[str, Any]],
    ) -> MemoryState:
        turns_text = "\n".join(f"{m['role']}: {m['content']}" for m in window)
        messages = self._prompts.render(
            "summarizer", summary=prior_summary or "(none)", turns=turns_text
        )
        result = await self._llm.generate(messages)
        summary_text = result.text.strip()
        await self._memory.add_summary(
            conversation_id=conversation_id,
            summary_text=summary_text,
            covered_through_turn=turn_id,
        )
        return MemoryState(
            summary=summary_text,
            recent_window=window,
            facts={},
            covered_through_turn=turn_id,
        )

    async def _extract_and_store_facts(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        window: list[dict[str, Any]],
        into: MemoryState,
    ) -> None:
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in window)
        try:
            extracted: _FactsResult = await self._llm.generate_structured(
                self._prompts.render("memory_updater", transcript=transcript),
                _FactsResult,
            )
        except Exception as exc:  # noqa: BLE001 - fact extraction is best-effort
            _logger.warning("Fact extraction failed: %s", exc)
            return
        for key, value in extracted.facts.items():
            if not key or not value:
                continue
            await self._memory.upsert_fact(
                org_id=org_id,
                user_id=user_id,
                fact_key=key,
                fact_value=value,
                source_conversation_id=conversation_id,
            )
            into.facts[key] = value


__all__ = ["MemoryService"]
