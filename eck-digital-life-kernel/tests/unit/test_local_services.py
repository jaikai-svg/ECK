from __future__ import annotations

from eck.runtime.local_services import LocalServiceManager


async def test_mock_brain_does_not_start_external_service(settings) -> None:
    manager = LocalServiceManager(settings)

    assert await manager.ensure_ollama() is True
    assert manager.status()["ollama"]["started_by_eck"] is False


async def test_offline_ollama_respects_disabled_auto_start(settings, monkeypatch) -> None:
    configured = settings.model_copy(
        update={"brain_provider": "ollama", "ollama_auto_start": False}
    )
    manager = LocalServiceManager(configured)

    async def unavailable() -> bool:
        return False

    monkeypatch.setattr(manager, "_ollama_health", unavailable)

    assert await manager.ensure_ollama() is False
    assert "automatic startup is disabled" in manager.status()["ollama"]["last_error"]
