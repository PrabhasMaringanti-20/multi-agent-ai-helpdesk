"""Prompt library / registry (ARCHITECTURE.md §4 registries.prompt_registry).

Holds the default, versioned prompt templates for every agent node and renders
them into provider ``ChatMessage`` lists. Prompts are configurable two ways:
in code (``DEFAULT_PROMPTS`` / ``register``) and from the database (the
``prompt_templates`` table, via ``load_from_db``) which overrides the code
defaults for the active version.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.providers.base import ChatMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # leave unknown placeholders blank
        return ""


def _safe_format(template: str, variables: dict[str, Any]) -> str:
    return string.Formatter().vformat(template, (), _SafeDict(variables))


@dataclass(frozen=True)
class PromptTemplate:
    key: str
    node_id: str
    system: str
    user_template: str = "{input}"
    version: int = 1
    variables: tuple[str, ...] = field(default_factory=tuple)

    def render(self, **variables: Any) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content=_safe_format(self.system, variables)),
            ChatMessage(role="user", content=_safe_format(self.user_template, variables)),
        ]


DEFAULT_PROMPTS: dict[str, PromptTemplate] = {
    "router": PromptTemplate(
        key="router",
        node_id="intent_classifier",
        system=(
            "You are the router for an enterprise IT helpdesk. Classify the user's "
            "message into exactly one category from this list: {categories}. Identify "
            "the intent, the sensitivity level (low|medium|high; payment and security "
            "are high), any control intent (greeting|cancel|human_request|none), and "
            "which required intake slots are still missing given {required_slots}. "
            "Return an intent_confidence in [0,1]."
        ),
        user_template="Conversation summary: {summary}\n\nUser message: {input}",
        variables=("categories", "required_slots", "summary", "input"),
    ),
    "retriever": PromptTemplate(
        key="retriever",
        node_id="query_planner",
        system=(
            "You plan retrieval for a helpdesk knowledge base. Rewrite the user's "
            "message into ONE concise, standalone keyword search query for category "
            "'{category}', resolving pronouns from the conversation summary. Return "
            "only the query text on a single line — no labels, no alternatives, no "
            "explanation, no quotes."
        ),
        user_template="Summary: {summary}\n\nMessage: {input}",
        variables=("category", "summary", "input"),
    ),
    "knowledge": PromptTemplate(
        key="knowledge",
        node_id="solution_synthesizer",
        system=(
            "You are an enterprise IT support assistant (Microsoft / ServiceNow style). "
            "Answer the question STRICTLY from the numbered SOURCES; never invent facts "
            "or steps. Cite sources inline as [n]. If the SOURCES lack a reliable "
            "answer, reply with exactly: ABSTAIN\n\n"
            "Output ONLY the answer using the Markdown template below (omit a section "
            "if it does not apply). Do NOT restate these instructions and do NOT show "
            "any reasoning or checklist — begin directly with '### Issue Detected':\n\n"
            "### Issue Detected\n"
            "<one or two sentences naming the problem>\n\n"
            "### Likely Cause\n"
            "<the most probable root cause, grounded [n]>\n\n"
            "### Recommended Steps\n"
            "- [ ] <first action, cite [n]>\n"
            "- [ ] <next action, cite [n]>\n\n"
            "### If the steps don't help\n"
            "<short fallback; use a '> **Warning:**' blockquote for anything risky>\n\n"
            "### Need more help?\n"
            "Ask a follow-up question or request a support ticket.\n"
            "> **Success:** <what a resolved state looks like>"
        ),
        user_template="Question: {input}\n\nSOURCES:\n{context}",
        variables=("input", "context"),
    ),
    "general": PromptTemplate(
        key="general",
        node_id="solution_synthesizer",
        system=(
            "You are an enterprise IT support assistant. Our knowledge base has no "
            "specific article for this question, so give helpful, safe, GENERAL IT "
            "guidance from common best practice. Begin with EXACTLY this line:\n"
            "> **Note:** General guidance — not from our knowledge base.\n\n"
            "Then answer with a short intro and a few clear numbered steps. If it "
            "needs account- or system-specific access, add one line saying a support "
            "ticket can be raised for a human. Do NOT invent company-specific details "
            "or cite sources."
        ),
        user_template="Question: {input}",
        variables=("input",),
    ),
    "clarification": PromptTemplate(
        key="clarification",
        node_id="info_collector",
        system=(
            "You collect missing information for category '{category}'. Ask for the "
            "missing slots {missing_slots} in a single, friendly, batched question. Do "
            "not ask for anything already provided in {filled_slots}."
        ),
        user_template="User message: {input}",
        variables=("category", "missing_slots", "filled_slots", "input"),
    ),
    "ticket": PromptTemplate(
        key="ticket",
        node_id="ticket_creator",
        system=(
            "Assemble an engineer-ready support ticket for category '{category}'. Produce "
            "a clear subject, a concise problem summary, suggested priority "
            "(low|medium|high|urgent), and helpful tags. Use only facts from the "
            "conversation; do not fabricate."
        ),
        user_template="Transcript:\n{transcript}\n\nCollected fields: {filled_slots}",
        variables=("category", "transcript", "filled_slots"),
    ),
    "summarizer": PromptTemplate(
        key="summarizer",
        node_id="memory_manager",
        system=(
            "Maintain a rolling summary of a support conversation. Merge the PRIOR "
            "SUMMARY with the NEW TURNS into a compact summary (<=150 words) capturing "
            "the problem, facts gathered, and any resolution. Preserve key identifiers."
        ),
        user_template="PRIOR SUMMARY:\n{summary}\n\nNEW TURNS:\n{turns}",
        variables=("summary", "turns"),
    ),
    "memory_updater": PromptTemplate(
        key="memory_updater",
        node_id="memory_manager",
        system=(
            "Extract durable, reusable facts about the user (e.g. default_device, "
            "vpn_client, os) from the conversation. Return only stable facts as "
            "key/value pairs; ignore one-off or sensitive values."
        ),
        user_template="Conversation:\n{transcript}",
        variables=("transcript",),
    ),
    "escalation": PromptTemplate(
        key="escalation",
        node_id="human_handoff",
        system=(
            "Write a handoff briefing for a human support engineer. Summarize the issue, "
            "what was already tried, the collected intake fields, and why the AI could "
            "not resolve it (reason code: {reason_code}). Be factual and concise."
        ),
        user_template="Transcript:\n{transcript}\n\nFields: {filled_slots}",
        variables=("reason_code", "transcript", "filled_slots"),
    ),
}


class PromptRegistry:
    """Resolves and renders prompt templates (code defaults + DB overrides)."""

    def __init__(self, prompts: dict[str, PromptTemplate] | None = None) -> None:
        self._prompts: dict[str, PromptTemplate] = dict(prompts or DEFAULT_PROMPTS)

    def keys(self) -> list[str]:
        return list(self._prompts)

    def get(self, key: str) -> PromptTemplate:
        if key not in self._prompts:
            raise KeyError(f"Unknown prompt template: {key}")
        return self._prompts[key]

    def render(self, key: str, **variables: Any) -> list[ChatMessage]:
        return self.get(key).render(**variables)

    def register(self, template: PromptTemplate) -> None:
        self._prompts[template.key] = template

    async def load_from_db(self, session: AsyncSession) -> int:
        """Override code defaults with active rows from ``prompt_templates``."""
        from sqlalchemy import select

        from app.models.registry import PromptTemplate as PromptRow

        result = await session.execute(select(PromptRow).where(PromptRow.is_active.is_(True)))
        overridden = 0
        for row in result.scalars().all():
            if row.key in self._prompts:
                base = self._prompts[row.key]
                self._prompts[row.key] = PromptTemplate(
                    key=row.key,
                    node_id=row.node_id,
                    system=row.content,
                    user_template=base.user_template,
                    version=row.version,
                    variables=base.variables,
                )
                overridden += 1
        return overridden


_registry: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


__all__ = ["PromptTemplate", "PromptRegistry", "get_prompt_registry", "DEFAULT_PROMPTS"]
