from __future__ import annotations

import re

_OPEN_QUESTION_MARKERS = (
    "?",
    "？",
    "unresolved",
    "unknown",
    "open question",
    "needs investigation",
    "requires investigation",
    "insufficient evidence",
    "尚待",
    "待查",
    "待驗證",
    "需查證",
    "需要研究",
    "未知",
    "未解",
    "證據不足",
)


def normalize_claim(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def unresolved_questions(next_step: str) -> list[str]:
    value = re.sub(r"\s+", " ", next_step).strip()
    if not value:
        return []
    normalized = value.casefold()
    if not any(marker in normalized for marker in _OPEN_QUESTION_MARKERS):
        return []
    return [value]


def unique_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result
