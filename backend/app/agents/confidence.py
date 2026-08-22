"""Confidence engine (ARCHITECTURE.md §1.3 / Phase 8).

Pure, deterministic scoring used by the grounding verifier + confidence gate:
answer confidence, retrieval confidence, hallucination risk, citation quality,
and a fused final confidence. No I/O — trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.utils import coalesce

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class ConfidenceReport:
    retrieval_confidence: float
    grounding_score: float
    citation_quality: float
    hallucination_risk: float
    answer_confidence: float
    final_confidence: float
    contradiction: bool


def retrieval_confidence(max_relevance_score: float) -> float:
    return _clamp01(max_relevance_score)


def citation_quality(answer_text: str, num_citations: int) -> float:
    """How well the answer is cited: markers present and backed by candidates."""
    if not answer_text.strip():
        return 0.0
    markers = {int(m) for m in _CITATION_RE.findall(answer_text)}
    if not markers:
        return 0.0
    if num_citations <= 0:
        return 0.0
    valid = [m for m in markers if 1 <= m <= num_citations]
    return _clamp01(len(valid) / len(markers))


def hallucination_risk(grounding_score: float, contradiction: bool) -> float:
    base = 1.0 - _clamp01(grounding_score)
    return _clamp01(base + (0.5 if contradiction else 0.0))


def answer_confidence(
    *,
    intent_confidence: float,
    retrieval_conf: float,
    grounding_score: float,
    citation_q: float,
    contradiction: bool,
) -> float:
    if contradiction:
        return 0.0
    weighted = (
        0.20 * _clamp01(intent_confidence)
        + 0.30 * _clamp01(retrieval_conf)
        + 0.35 * _clamp01(grounding_score)
        + 0.15 * _clamp01(citation_q)
    )
    return _clamp01(weighted)


def evaluate(
    *,
    intent_confidence: float | None,
    max_relevance_score: float,
    grounding_score: float | None,
    contradiction: bool,
    answer_text: str,
    num_citations: int,
) -> ConfidenceReport:
    intent = coalesce(intent_confidence, 0.5) or 0.0
    grounding = coalesce(grounding_score, 0.0) or 0.0
    retr = retrieval_confidence(max_relevance_score)
    cite_q = citation_quality(answer_text, num_citations)
    risk = hallucination_risk(grounding, contradiction)
    ans = answer_confidence(
        intent_confidence=intent,
        retrieval_conf=retr,
        grounding_score=grounding,
        citation_q=cite_q,
        contradiction=contradiction,
    )
    return ConfidenceReport(
        retrieval_confidence=round(retr, 4),
        grounding_score=round(grounding, 4),
        citation_quality=round(cite_q, 4),
        hallucination_risk=round(risk, 4),
        answer_confidence=round(ans, 4),
        final_confidence=round(ans, 4),
        contradiction=contradiction,
    )


__all__ = [
    "ConfidenceReport",
    "retrieval_confidence",
    "citation_quality",
    "hallucination_risk",
    "answer_confidence",
    "evaluate",
]
