"""Deterministic text chunking with overlap (used by ingestion)."""

from __future__ import annotations


def chunk_text(text: str, *, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """Split ``text`` into word-bounded chunks of ~``chunk_size`` chars with overlap."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(start + chunk_size, length)
        # Prefer a word boundary near the end of the window.
        if end < length:
            space = normalized.rfind(" ", start, end)
            if space > start:
                end = space
        chunks.append(normalized[start:end].strip())
        if end >= length:
            break
        start = max(0, end - overlap)
    return [c for c in chunks if c]


__all__ = ["chunk_text"]
