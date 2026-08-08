from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class CommunitySourceCatalog:
    _trust_tiers = {"official-specification", "official-maintainer"}
    _adoption_modes = {"research-only", "pattern-candidate"}

    def __init__(self, path: Path) -> None:
        self.path = path

    def status(self) -> dict[str, Any]:
        sources = self.list_sources()
        return {
            "schema_version": "eck-community-sources.v1",
            "available": bool(sources),
            "source_count": len(sources),
            "pattern_candidates": sum(
                item["adoption_mode"] == "pattern-candidate" for item in sources
            ),
            "policy": (
                "Sources are research inputs only. ECK must synthesize an ECK-native skill, "
                "pass isolated tests and regressions, and preserve license provenance before "
                "activation."
            ),
            "items": sources,
        }

    def list_sources(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        items = payload.get("sources", [])
        if not isinstance(items, list):
            return []
        return [validated for item in items if (validated := self._validate(item))]

    def match(self, topic: str) -> dict[str, Any] | None:
        topic_terms = self._terms(topic)
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for item in self.list_sources():
            source_terms = self._terms(" ".join(item["topics"]))
            score = len(topic_terms & source_terms)
            if score:
                ranked.append((score, item["source_id"], item))
        if not ranked:
            return None
        ranked.sort(key=lambda value: (-value[0], value[1]))
        return ranked[0][2]

    @classmethod
    def _validate(cls, item: object) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        required = ("source_id", "name", "url", "owner", "trust_tier", "license")
        if any(not str(item.get(name, "")).strip() for name in required):
            return None
        url = str(item["url"]).strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            return None
        trust_tier = str(item["trust_tier"])
        adoption_mode = str(item.get("adoption_mode", "research-only"))
        topics = item.get("topics", [])
        if trust_tier not in cls._trust_tiers or adoption_mode not in cls._adoption_modes:
            return None
        if not isinstance(topics, list) or not all(isinstance(value, str) for value in topics):
            return None
        return {
            "source_id": str(item["source_id"]),
            "name": str(item["name"]),
            "url": url,
            "owner": str(item["owner"]),
            "trust_tier": trust_tier,
            "license": str(item["license"]),
            "adoption_mode": adoption_mode,
            "topics": topics,
        }

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {
            term
            for term in re.findall(r"[a-z][a-z0-9-]+", value.casefold())
            if len(term) >= 3
        }
