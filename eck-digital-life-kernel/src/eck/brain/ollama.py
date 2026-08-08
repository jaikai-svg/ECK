from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx

from eck.brain.arbiter import InferenceArbiter
from eck.brain.base import BrainHealth, BrainProvider, BrainResponse


class OllamaBrainProvider(BrainProvider):
    def __init__(
        self,
        base_url: str,
        model: str | None,
        timeout_seconds: float,
        *,
        arbiter: InferenceArbiter | None = None,
        default_priority: int = 20,
        health_cache_seconds: float = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.arbiter = arbiter or InferenceArbiter()
        self.default_priority = default_priority
        self.health_cache_seconds = health_cache_seconds
        self._health_cache: BrainHealth | None = None
        self._health_checked_at = 0.0

    def resource_slot(self, priority: int) -> AbstractAsyncContextManager[None]:
        return self.arbiter.slot(priority)

    async def health(self) -> BrainHealth:
        now = asyncio.get_running_loop().time()
        if (
            self._health_cache is not None
            and now - self._health_checked_at < self.health_cache_seconds
        ):
            return self._health_cache
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = [
                    item.get("name")
                    for item in response.json().get("models", [])
                    if item.get("name")
                ]
            if self.model is None:
                health = BrainHealth(
                    provider="ollama",
                    available=False,
                    detail=(
                        "Ollama is reachable, but ECK_OLLAMA_MODEL is not configured. "
                        f"Available models: {', '.join(models) or 'none'}"
                    ),
                )
                return self._cache_health(now, health)
            available = self.model in models or any(
                name and name.split(":")[0] == self.model for name in models
            )
            health = BrainHealth(
                provider="ollama",
                available=available,
                model=self.model,
                detail=(
                    "Ollama model is ready."
                    if available
                    else (
                        "Configured model is not installed. Available: "
                        f"{', '.join(models) or 'none'}"
                    )
                ),
            )
            return self._cache_health(now, health)
        except (httpx.HTTPError, ValueError) as exc:
            health = BrainHealth(
                provider="ollama",
                available=False,
                model=self.model,
                detail=f"Ollama is unavailable: {exc}",
            )
            return self._cache_health(now, health)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        if not self.model:
            raise RuntimeError("ECK_OLLAMA_MODEL must be configured before chat is used.")
        generation_options: dict[str, Any] = {"temperature": 0}
        if options:
            generation_options.update(options)
        priority = int(generation_options.pop("_priority", self.default_priority))
        think = generation_options.pop("think", None)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": generation_options,
        }
        if isinstance(think, bool):
            payload["think"] = think
        if format_schema:
            payload["format"] = format_schema
        async with self.arbiter.slot(priority), httpx.AsyncClient(
            timeout=self.timeout
        ) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            raw = response.json()
        return BrainResponse(
            content=str(raw.get("message", {}).get("content", "")),
            model=str(raw.get("model", self.model)),
            raw=raw,
        )

    def _cache_health(self, checked_at: float, health: BrainHealth) -> BrainHealth:
        self._health_cache = health
        self._health_checked_at = checked_at
        return health
