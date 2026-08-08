from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from importlib import import_module
from typing import Any

from eck.research.dedup import normalize_document_text


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    title: str
    text: str
    method: str
    published_at: str | None = None
    author: str | None = None


class _FallbackExtractor(HTMLParser):
    _ignored_tags = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.title: list[str] = []
        self.metadata: dict[str, str] = {}
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._ignored_tags:
            self._ignored_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            values = {key.casefold(): value or "" for key, value in attrs}
            name = (values.get("property") or values.get("name") or "").casefold()
            content = values.get("content", "").strip()
            if name and content:
                self.metadata[name] = content

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title.append(value)
        self.text.append(value)


def extract_document(
    body: bytes,
    *,
    url: str,
    content_type: str,
    max_chars: int,
) -> ExtractedDocument:
    decoded = body.decode("utf-8", errors="replace")
    if "html" not in content_type.casefold():
        text = normalize_document_text(decoded)[:max_chars]
        return ExtractedDocument(title="", text=text, method="plain-text")
    extracted = _extract_with_trafilatura(decoded, url)
    if extracted is not None and extracted.text:
        return ExtractedDocument(
            title=extracted.title[:500],
            text=extracted.text[:max_chars],
            method=extracted.method,
            published_at=extracted.published_at,
            author=extracted.author,
        )
    parser = _FallbackExtractor()
    parser.feed(decoded)
    title = parser.metadata.get("og:title") or " ".join(parser.title)
    published_at = (
        parser.metadata.get("article:published_time")
        or parser.metadata.get("date")
        or None
    )
    author = parser.metadata.get("author") or None
    return ExtractedDocument(
        title=" ".join(title.split())[:500],
        text=normalize_document_text("\n".join(parser.text))[:max_chars],
        method="html-parser-fallback",
        published_at=published_at,
        author=author,
    )


def _extract_with_trafilatura(html: str, url: str) -> ExtractedDocument | None:
    try:
        module: Any = import_module("trafilatura")
        document = module.bare_extraction(
            html,
            url=url,
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        return None
    if document is None:
        return None
    data = document.as_dict() if hasattr(document, "as_dict") else document
    if not isinstance(data, dict):
        return None
    text = normalize_document_text(str(data.get("text", "")))
    if not text:
        return None
    return ExtractedDocument(
        title=" ".join(str(data.get("title", "")).split()),
        text=text,
        method="trafilatura",
        published_at=str(data.get("date")) if data.get("date") else None,
        author=str(data.get("author")) if data.get("author") else None,
    )
