"""HTML + plain-text extraction (stdlib only)."""

from __future__ import annotations

from html.parser import HTMLParser

from app.core.exceptions import ValidationError

_SKIP_TAGS = {"script", "style", "head", "meta", "link"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and data.strip():
            self._chunks.append(data.strip())

    @property
    def text(self) -> str:
        return " ".join(self._chunks)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def parse_html(data: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(_decode(data))
    text = parser.text
    if not text.strip():
        raise ValidationError("HTML contained no extractable text.")
    return text


def parse_text(data: bytes) -> str:
    text = _decode(data)
    if not text.strip():
        raise ValidationError("Document contained no text.")
    return text


__all__ = ["parse_html", "parse_text"]
