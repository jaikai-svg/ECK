from __future__ import annotations

import json
from typing import Any

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse


class MockBrainProvider(BrainProvider):
    """Deterministic provider used by tests and offline demonstrations."""

    async def health(self) -> BrainHealth:
        return BrainHealth(provider="mock", available=True, model="mock-deterministic")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        content = json.dumps(
            {
                "summary": "Mock brain response",
                "message_count": len(messages),
                "schema_requested": format_schema is not None,
            },
            ensure_ascii=False,
        )
        return BrainResponse(
            content=content,
            model="mock-deterministic",
            raw={"messages": messages, "options": options or {}},
        )
