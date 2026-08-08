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
    last_json = None
    get_count = 0

    def __init__(self, *, timeout) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        type(self).get_count += 1
        return _FakeResponse({"models": [{"name": "qwen:test"}]})

    async def post(self, url, json):
        type(self).last_json = json
        return _FakeResponse(
            {"model": json["model"], "message": {"content": "verified response"}}
        )


@pytest.mark.asyncio
async def test_ollama_health_and_chat(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.get_count = 0
    brain = OllamaBrainProvider("http://ollama", "qwen:test", 10)
    health = await brain.health()
    assert health.available
    assert (await brain.health()).available
    assert _FakeClient.get_count == 1
    response = await brain.chat(
        [{"role": "user", "content": "hello"}],
        options={"num_predict": 128, "num_ctx": 2048, "num_gpu": 0, "think": False},
    )
    assert response.content == "verified response"
    assert _FakeClient.last_json["options"] == {
        "temperature": 0,
        "num_predict": 128,
        "num_ctx": 2048,
        "num_gpu": 0,
    }
    assert _FakeClient.last_json["think"] is False


@pytest.mark.asyncio
async def test_ollama_requires_explicit_model(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    brain = OllamaBrainProvider("http://ollama", None, 10)
    health = await brain.health()
    assert not health.available
    with pytest.raises(RuntimeError):
        await brain.chat([])
