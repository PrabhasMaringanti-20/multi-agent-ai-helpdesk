"""Document parsers for KB ingestion (PDF / DOCX / HTML+text)."""

from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ValidationError
from app.rag.parsers.docx_parser import parse_docx
from app.rag.parsers.html_text_parser import parse_html, parse_text
from app.rag.parsers.pdf_parser import parse_pdf

_EXTENSION_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".html": parse_html,
    ".htm": parse_html,
    ".txt": parse_text,
    ".md": parse_text,
}

_CONTENT_TYPE_PARSERS = {
    "application/pdf": parse_pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": parse_docx,
    "text/html": parse_html,
    "text/plain": parse_text,
    "text/markdown": parse_text,
}


def parse_document(
    data: bytes, *, filename: str | None = None, content_type: str | None = None
) -> str:
    """Extract plain text from a document, selecting the parser by type/extension."""
    if content_type and content_type in _CONTENT_TYPE_PARSERS:
        return _CONTENT_TYPE_PARSERS[content_type](data)
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in _EXTENSION_PARSERS:
            return _EXTENSION_PARSERS[suffix](data)
    raise ValidationError(
        f"Unsupported document type (filename={filename!r}, content_type={content_type!r})."
    )


__all__ = ["parse_document", "parse_pdf", "parse_docx", "parse_html", "parse_text"]
