"""PDF text extraction (pypdf, imported lazily)."""

from __future__ import annotations

import io

from app.core.exceptions import ValidationError


def parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValidationError("pypdf is not installed.") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Failed to parse PDF: {exc}") from exc

    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text.strip():
        raise ValidationError("PDF contained no extractable text (it may be scanned).")
    return text


__all__ = ["parse_pdf"]
