from __future__ import annotations

import re


def capability_equivalent(requested: str, available: str) -> bool:
    requested_key = _capability_key(requested)
    available_key = _capability_key(available)
    if not requested_key or not available_key:
        return False
    if requested_key == available_key:
        return True
    requested_tokens = set(requested_key.split("."))
    available_tokens = set(available_key.split("."))
    overlap = requested_tokens & available_tokens
    return len(overlap) >= 2 and len(overlap) / min(
        len(requested_tokens), len(available_tokens)
    ) >= 0.66


def _capability_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", ".", value.strip().lower()).strip(".")
    aliases = {
        "public": "public",
        "webpage": "web",
        "website": "web",
        "browsing": "explore",
        "exploration": "explore",
    }
    return ".".join(aliases.get(token, token) for token in normalized.split("."))
