from __future__ import annotations

from typing import Any

import httpx


class GitHubToolDiscovery:
    _categories = (
        ("browser-automation", "topic:browser-automation"),
        ("web-extraction", "topic:web-scraping"),
        ("developer-tools", "topic:developer-tools"),
        ("testing", "topic:testing"),
        ("agent-orchestration", "topic:ai-agents"),
        ("rag", "topic:rag"),
        ("document-processing", "topic:document-processing"),
        ("data-analysis", "topic:data-analysis"),
        ("workflow-automation", "topic:automation"),
        ("media-processing", "topic:media-processing"),
    )
    _license_queries = (
        "license:mit",
        "license:apache-2.0",
        "license:bsd-2-clause",
        "license:bsd-3-clause",
    )

    def __init__(self, *, minimum_stars: int, timeout_seconds: float = 30) -> None:
        self.minimum_stars = minimum_stars
        self.timeout_seconds = timeout_seconds

    async def discover(
        self,
        *,
        excluded_repositories: set[str],
        cursor: int,
    ) -> dict[str, Any] | None:
        combination_count = len(self._categories) * len(self._license_queries)
        combination = cursor % combination_count
        page = (cursor // combination_count) % 5 + 1
        category, category_query = self._categories[combination % len(self._categories)]
        license_query = self._license_queries[
            (combination // len(self._categories)) % len(self._license_queries)
        ]
        query = (
            f"{category_query} {license_query} stars:>={self.minimum_stars} "
            "fork:false archived:false"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ECK-Digital-Life-Kernel/0.1 verified-tool-campaign",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            headers=headers,
        ) as client:
            response = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": "20",
                    "page": str(page),
                },
            )
            response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("full_name") or "").strip()
            if (
                not full_name
                or full_name.casefold() in excluded_repositories
                or item.get("private")
                or item.get("archived")
                or item.get("fork")
                or int(item.get("stargazers_count") or 0) < self.minimum_stars
            ):
                continue
            url = str(item.get("html_url") or "")
            if not url.startswith("https://github.com/"):
                continue
            return {
                "name": full_name,
                "url": url,
                "description": str(item.get("description") or "")[:1000],
                "stars": int(item.get("stargazers_count") or 0),
                "category": category,
                "query": query,
                "query_page": page,
            }
        return None
