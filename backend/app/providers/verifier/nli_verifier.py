"""LLM-as-judge entailment verifier (reliability gate #2 support).

Wraps a (small-tier) ``LLMProvider`` to judge whether a claim is fully supported
by the retrieved sources, returning a structured ``VerifierResult``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.base import ChatMessage, LLMProvider, VerifierResult

_SYSTEM = (
    "You are a strict fact-verification judge for an IT helpdesk. Decide whether "
    "the CLAIM is fully supported by the provided SOURCES. Do not use outside "
    "knowledge. If any part of the claim is unsupported or contradicted, it is "
    "NOT entailed. Return a faithfulness score in [0,1]."
)


class _Verdict(BaseModel):
    entailed: bool = Field(default=False)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="")


class LLMVerifier:
    """VerifierProvider implementation backed by an LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def verify(self, claim: str, sources: list[str]) -> VerifierResult:
        if not sources:
            return VerifierResult(entailed=False, score=0.0, rationale="no sources")
        rendered = "\n\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sources))
        messages = [
            ChatMessage(role="system", content=_SYSTEM),
            ChatMessage(
                role="user",
                content=f"CLAIM:\n{claim}\n\nSOURCES:\n{rendered}\n\n"
                "Is the claim fully supported by the sources?",
            ),
        ]
        verdict: _Verdict = await self._llm.generate_structured(messages, _Verdict)
        return VerifierResult(
            entailed=verdict.entailed, score=verdict.score, rationale=verdict.rationale
        )


__all__ = ["LLMVerifier"]
