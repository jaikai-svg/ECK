from __future__ import annotations

import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    url: str
    title: str
    provider: str
    published_at: str | None = None
    language: str | None = None
    source_country: str | None = None


class SourceDiscovery(Protocol):
    async def search(
        self,
        query: str,
        *,
        timespan: str,
        limit: int,
    ) -> list[DiscoveryCandidate]: ...


class FallbackDiscoveryClient:
    def __init__(self, *providers: SourceDiscovery) -> None:
        if not providers:
            raise ValueError("At least one discovery provider is required.")
        self.providers = providers

    async def search(
        self,
        query: str,
        *,
        timespan: str,
        limit: int,
    ) -> list[DiscoveryCandidate]:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                candidates = await provider.search(
                    query,
                    timespan=timespan,
                    limit=limit,
                )
            except (
                httpx.HTTPError,
                json.JSONDecodeError,
                ET.ParseError,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                last_error = exc
                continue
            if candidates:
                return candidates
        if last_error is not None:
            raise last_error
        return []


class BingNewsRSSDiscoveryClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        base_url: str = "https://www.bing.com/news/search",
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname != "www.bing.com":
            raise ValueError("Bing News discovery is restricted to the public HTTPS RSS feed.")
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def search(
        self,
        query: str,
        *,
        timespan: str,
        limit: int,
    ) -> list[DiscoveryCandidate]:
        del timespan
        cleaned_query = GDELTDiscoveryClient._clean_query(query)
        if len(cleaned_query) < 2:
            return []
        headers = {"User-Agent": "ECK-Digital-Life-Kernel/0.1 critical-research"}
        async with self._request_lock:
            for variant in self._query_variants(cleaned_query):
                delay = max(0.0, 2.0 - (time.monotonic() - self._last_request_at))
                if delay:
                    await asyncio.sleep(delay)
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    headers=headers,
                ) as client:
                    response = await client.get(
                        self.base_url,
                        params={"q": variant, "format": "rss"},
                    )
                self._last_request_at = time.monotonic()
                response.raise_for_status()
                candidates = self._parse_response(response.content, limit=limit)
                if candidates:
                    return candidates
        return []

    @staticmethod
    def _parse_response(content: bytes, *, limit: int) -> list[DiscoveryCandidate]:
        if len(content) > 2_000_000:
            raise ValueError("Bing News RSS response exceeded the 2 MB safety limit.")
        root = ET.fromstring(content)
        candidates: list[DiscoveryCandidate] = []
        for item in root.findall(".//item"):
            url = (item.findtext("link") or "").strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            candidates.append(
                DiscoveryCandidate(
                    url=url,
                    title=" ".join((item.findtext("title") or "").split())[:500],
                    provider="Bing News RSS",
                    published_at=(item.findtext("pubDate") or "").strip() or None,
                )
            )
        return candidates[: max(1, min(limit, 50))]

    @staticmethod
    def _query_variants(query: str) -> list[str]:
        subject = query.split(":", 1)[0]
        words = re.findall(r"[A-Za-z][A-Za-z-]*", subject)
        ignored = {
            "and",
            "current",
            "evidence",
            "credibility",
            "latest",
            "recent",
            "research",
            "report",
            "reports",
            "study",
            "studies",
            "the",
            "in",
            "on",
            "under",
        }
        meaningful = [word for word in words if word.casefold() not in ignored]
        variants: list[str] = []
        if len(meaningful) >= 4:
            variants.extend((" ".join(meaningful[-2:]), " ".join(meaningful[:2])))
        elif len(meaningful) >= 2:
            variants.append(" ".join(meaningful))
        variants.append(query)
        return list(dict.fromkeys(variant for variant in variants if len(variant) >= 2))


class GDELTDiscoveryClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc",
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname != "api.gdeltproject.org":
            raise ValueError("Current-information discovery is restricted to GDELT HTTPS.")
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def search(
        self,
        query: str,
        *,
        timespan: str,
        limit: int,
    ) -> list[DiscoveryCandidate]:
        cleaned_query = self._clean_query(query)
        if len(cleaned_query) < 2:
            return []
        if not re.fullmatch(r"\d{1,3}(?:min|h|d|w|m)", timespan.casefold()):
            raise ValueError("GDELT timespan must look like 24h, 7d, 2w, or 3m.")
        params = {
            "query": cleaned_query,
            "mode": "artlist",
            "maxrecords": str(max(1, min(limit, 50))),
            "timespan": timespan.casefold(),
            "sort": "datedesc",
            "format": "json",
        }
        headers = {"User-Agent": "ECK-Digital-Life-Kernel/0.1 critical-research"}
        response: httpx.Response | None = None
        for attempt in range(3):
            async with self._request_lock:
                delay = max(0.0, 5.0 - (time.monotonic() - self._last_request_at))
                if delay:
                    await asyncio.sleep(delay)
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    headers=headers,
                ) as client:
                    response = await client.get(self.base_url, params=params)
                self._last_request_at = time.monotonic()
            if response.status_code not in {429, 503}:
                response.raise_for_status()
                break
            if attempt == 2:
                response.raise_for_status()
            retry_after = response.headers.get("retry-after", "")
            wait_seconds = float(retry_after) if retry_after.isdigit() else 5.0 * (attempt + 1)
            await asyncio.sleep(min(30.0, wait_seconds))
        if response is None:
            raise RuntimeError("GDELT discovery did not produce a response.")
        if len(response.content) > 2_000_000:
            raise ValueError("GDELT discovery response exceeded the 2 MB safety limit.")
        payload = json.loads(response.content)
        articles = payload.get("articles", []) if isinstance(payload, dict) else []
        candidates: list[DiscoveryCandidate] = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            url = str(article.get("url", "")).strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            candidates.append(
                DiscoveryCandidate(
                    url=url,
                    title=" ".join(str(article.get("title", "")).split())[:500],
                    provider="GDELT DOC 2.0",
                    published_at=str(article.get("seendate") or "") or None,
                    language=str(article.get("language") or "") or None,
                    source_country=str(article.get("sourcecountry") or "") or None,
                )
            )
        return candidates[:limit]

    @staticmethod
    def _clean_query(value: str) -> str:
        value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value[:300]
