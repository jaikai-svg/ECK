from __future__ import annotations

from typing import Any

import httpx

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse


class OllamaBrainProvider(BrainProvider):
    def __init__(self, base_url: str, model: str | None, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds

    async def health(self) -> BrainHealth:
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
                return BrainHealth(
                    provider="ollama",
                    available=False,
                    detail=(
                        "Ollama is reachable, but ECK_OLLAMA_MODEL is not configured. "
                        f"Available models: {', '.join(models) or 'none'}"
                    ),
                )
            available = self.model in models or any(
                name and name.split(":")[0] == self.model for name in models
            )
            return BrainHealth(
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
        except (httpx.HTTPError, ValueError) as exc:
            return BrainHealth(
                provider="ollama",
                available=False,
                model=self.model,
                detail=f"Ollama is unavailable: {exc}",
            )

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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            raw = response.json()
        return BrainResponse(
            content=str(raw.get("message", {}).get("content", "")),
            model=str(raw.get("model", self.model)),
            raw=raw,
        )
