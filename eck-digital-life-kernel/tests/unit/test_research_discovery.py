from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import eck.research.content as content_module
import eck.research.discovery as discovery_module
from eck.research.content import extract_document
from eck.research.discovery import (
    BingNewsRSSDiscoveryClient,
    DiscoveryCandidate,
    FallbackDiscoveryClient,
    GDELTDiscoveryClient,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = json.dumps(payload).encode()
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, **kwargs: Any) -> None:
        self.options = kwargs

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, str]) -> _Response:
        assert url == "https://api.gdeltproject.org/api/v2/doc/doc"
        assert params["mode"] == "artlist"
        return _Response(
            {
                "articles": [
                    {
                        "url": "https://news.example/article",
                        "title": "  Verified   current report ",
                        "seendate": "20260808T010000Z",
                        "language": "English",
                        "sourcecountry": "Taiwan",
                    },
                    {"url": "ftp://invalid.example/file", "title": "Invalid"},
                    "not-an-object",
                ]
            }
        )


class _RetryClient(_Client):
    calls = 0

    async def get(self, url: str, params: dict[str, str]) -> _Response:
        del url, params
        type(self).calls += 1
        if type(self).calls == 1:
            response = _Response({})
            response.status_code = 429
            response.headers = {"retry-after": "0"}
            return response
        return _Response(
            {
                "articles": [
                    {
                        "url": "https://news.example/recovered",
                        "title": "Recovered after rate limit",
                    }
                ]
            }
        )


class _RSSClient(_Client):
    async def get(self, url: str, params: dict[str, str]) -> _Response:
        assert url == "https://www.bing.com/news/search"
        assert params == {"q": "economic evidence", "format": "rss"}
        response = _Response({})
        response.content = b"""<?xml version="1.0"?><rss><channel><item>
            <title>Independent economic report</title>
            <link>https://news.example/economic-report</link>
            <pubDate>Sat, 08 Aug 2026 01:00:00 GMT</pubDate>
        </item></channel></rss>"""
        return response


class _EmptyDiscovery:
    async def search(
        self, query: str, *, timespan: str, limit: int
    ) -> list[DiscoveryCandidate]:
        del query, timespan, limit
        return []


class _FixedDiscovery:
    async def search(
        self, query: str, *, timespan: str, limit: int
    ) -> list[DiscoveryCandidate]:
        del query, timespan, limit
        return [
            DiscoveryCandidate(
                url="https://fallback.example/report",
                title="Fallback report",
                provider="fallback",
            )
        ]


@pytest.mark.asyncio
async def test_gdelt_discovery_normalizes_and_filters_candidates(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = GDELTDiscoveryClient(timeout_seconds=12)

    candidates = await client.search(
        " energy\x00 transition ",
        timespan="7D",
        limit=100,
    )

    assert len(candidates) == 1
    assert candidates[0].title == "Verified current report"
    assert candidates[0].provider == "GDELT DOC 2.0"
    assert candidates[0].source_country == "Taiwan"


@pytest.mark.asyncio
async def test_gdelt_discovery_rejects_invalid_inputs() -> None:
    client = GDELTDiscoveryClient()

    assert await client.search(" ", timespan="7d", limit=5) == []
    with pytest.raises(ValueError, match="timespan"):
        await client.search("energy", timespan="yesterday", limit=5)
    with pytest.raises(ValueError, match="restricted"):
        GDELTDiscoveryClient(base_url="https://example.com/search")


@pytest.mark.asyncio
async def test_gdelt_discovery_retries_rate_limits(monkeypatch) -> None:
    async def no_wait(seconds: float) -> None:
        del seconds

    _RetryClient.calls = 0
    monkeypatch.setattr(httpx, "AsyncClient", _RetryClient)
    monkeypatch.setattr(discovery_module.asyncio, "sleep", no_wait)

    candidates = await GDELTDiscoveryClient().search(
        "current report",
        timespan="7d",
        limit=5,
    )

    assert _RetryClient.calls == 2
    assert candidates[0].url == "https://news.example/recovered"


@pytest.mark.asyncio
async def test_bing_rss_and_fallback_discovery(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _RSSClient)
    bing = BingNewsRSSDiscoveryClient()

    candidates = await bing.search("economic evidence", timespan="7d", limit=5)
    fallback = await FallbackDiscoveryClient(
        _EmptyDiscovery(), _FixedDiscovery()
    ).search("topic", timespan="7d", limit=5)

    assert candidates[0].provider == "Bing News RSS"
    assert candidates[0].url == "https://news.example/economic-report"
    assert fallback[0].provider == "fallback"
    assert BingNewsRSSDiscoveryClient._query_variants(
        "business management and organizational effectiveness: current evidence"
    )[0] == "organizational effectiveness"


def test_content_extractor_supports_plain_text_and_trafilatura(monkeypatch) -> None:
    plain = extract_document(
        b"first line\n\nsecond line",
        url="https://example.com/plain",
        content_type="text/plain",
        max_chars=100,
    )

    class _Document:
        def as_dict(self) -> dict[str, str]:
            return {
                "title": "Structured report",
                "text": "Clean extracted article body.",
                "date": "2026-08-08",
                "author": "Researcher",
            }

    class _Trafilatura:
        @staticmethod
        def bare_extraction(*args: object, **kwargs: object) -> _Document:
            return _Document()

    monkeypatch.setattr(content_module, "import_module", lambda name: _Trafilatura)
    html = extract_document(
        b"<html><body>noisy fallback</body></html>",
        url="https://example.com/report",
        content_type="text/html",
        max_chars=100,
    )

    assert plain.text == "first line\nsecond line"
    assert plain.method == "plain-text"
    assert html.method == "trafilatura"
    assert html.title == "Structured report"
    assert html.author == "Researcher"


def test_content_extractor_falls_back_and_ignores_active_markup(monkeypatch) -> None:
    def unavailable(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(content_module, "import_module", unavailable)
    document = extract_document(
        (
            b"<html><head><title>Fallback report</title>"
            b'<meta name="author" content="Analyst">'
            b'<meta property="article:published_time" content="2026-08-08">'
            b"<script>malicious instruction</script></head>"
            b"<body><article>Retained factual article body for verification. "
            b"Additional context makes the document useful.</article></body></html>"
        ),
        url="https://example.com/fallback",
        content_type="text/html",
        max_chars=1000,
    )

    assert document.method == "html-parser-fallback"
    assert document.title == "Fallback report"
    assert document.author == "Analyst"
    assert document.published_at == "2026-08-08"
    assert "malicious instruction" not in document.text
