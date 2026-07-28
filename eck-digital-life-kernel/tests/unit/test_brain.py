from __future__ import annotations

import httpx
import pytest

from eck.brain.mock import MockBrainProvider
from eck.brain.ollama import OllamaBrainProvider


@pytest.mark.asyncio
async def test_mock_brain_is_deterministic() -> None:
    brain = MockBrainProvider()
    assert (await brain.health()).available
    response = await brain.chat([{"role": "user", "content": "hello"}])
    assert response.model == "mock-deterministic"
    assert "message_count" in response.content


class _FakeResponse:
    def __init__(self, payload, *, error: bool = False) -> None:
        self.payload = payload
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise httpx.HTTPStatusError(
                "failed",
                request=httpx.Request("GET", "http://ollama"),
                response=httpx.Response(500),
            )

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, *, timeout) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        return _FakeResponse({"models": [{"name": "qwen:test"}]})

    async def post(self, url, json):
        return _FakeResponse(
            {"model": json["model"], "message": {"content": "verified response"}}
        )


@pytest.mark.asyncio
async def test_ollama_health_and_chat(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    brain = OllamaBrainProvider("http://ollama", "qwen:test", 10)
    health = await brain.health()
    assert health.available
    response = await brain.chat([{"role": "user", "content": "hello"}])
    assert response.content == "verified response"


@pytest.mark.asyncio
async def test_ollama_requires_explicit_model(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    brain = OllamaBrainProvider("http://ollama", None, 10)
    health = await brain.health()
    assert not health.available
    with pytest.raises(RuntimeError):
        await brain.chat([])

