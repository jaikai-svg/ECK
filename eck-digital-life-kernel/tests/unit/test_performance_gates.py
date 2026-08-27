from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import httpx

from eck.api.main import create_api
from eck.brain.base import BrainHealth


class _HealthProbe:
    def __init__(
        self,
        name: str,
        counter: list[str],
        ready: asyncio.Event,
        expected: int,
    ) -> None:
        self.name = name
        self.counter = counter
        self.ready = ready
        self.expected = expected

    async def health(self) -> BrainHealth:
        self.counter.append(self.name)
        if len(self.counter) == self.expected:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=0.5)
        return BrainHealth(provider="test", available=True, model=self.name)


async def _get(api, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _coordinated_status(
    name: str,
    counter: list[str],
    ready: asyncio.Event,
    value: dict[str, object],
) -> dict[str, object]:
    counter.append(name)
    if len(counter) == 3:
        ready.set()
    await asyncio.wait_for(ready.wait(), timeout=0.5)
    return value


async def test_health_provider_checks_are_concurrent(application) -> None:
    counter: list[str] = []
    ready = asyncio.Event()
    mutable_application = cast(Any, application)
    mutable_application.brain = _HealthProbe("brain", counter, ready, 2)
    mutable_application.coder_brain = _HealthProbe("coder", counter, ready, 2)

    response = await _get(create_api(application=application), "/health")

    assert response.status_code == 200
    assert set(counter) == {"brain", "coder"}


async def test_roadmap_external_status_checks_are_concurrent(application, monkeypatch) -> None:
    counter: list[str] = []
    ready = asyncio.Event()
    cast(Any, application).coder_brain = _HealthProbe("coder", counter, ready, 3)

    async def skill_bridge_status() -> dict[str, object]:
        return await _coordinated_status(
            "skill-bridge",
            counter,
            ready,
            {"conversion_verified": False, "active_generated_skills": 0},
        )

    async def project_lab_status() -> dict[str, object]:
        return await _coordinated_status(
            "project-lab",
            counter,
            ready,
            {
                "project_count": 0,
                "published_count": 0,
                "github": {"ready": False},
            },
        )

    monkeypatch.setattr(application.skill_bridge, "status", skill_bridge_status)
    monkeypatch.setattr(application.project_lab, "status", project_lab_status)

    response = await _get(create_api(application=application), "/v1/roadmap")

    assert response.status_code == 200
    assert set(counter) == {"skill-bridge", "project-lab", "coder"}


def test_sqlite_wal_serializes_concurrent_event_writers(application) -> None:
    with sqlite3.connect(application.store.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    def append(index: int) -> None:
        application.store.append_event("ConcurrentWrite", f"writer-{index}", {"index": index})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(8)))

    valid, failed_sequence = application.store.verify_event_chain()
    assert application.store.count_events() == 8
    assert valid
    assert failed_sequence is None


def test_workspace_frontend_obeys_idle_request_and_bundle_budgets() -> None:
    root = Path(__file__).resolve().parents[2]
    dashboard = root / "src" / "eck" / "dashboard"
    budget = json.loads(
        (root / "config" / "performance-budgets.json").read_text(encoding="utf-8")
    )
    index = (dashboard / "index.html").read_text(encoding="utf-8")
    source = (dashboard / "src" / "workspace.ts").read_text(encoding="utf-8")
    initial_files = (
        dashboard / "index.html",
        dashboard / "styles.css",
        dashboard / "modules" / "workspace.js",
        dashboard / "modules" / "workspace_components.js",
        dashboard / "modules" / "workspace_state.js",
        dashboard / "modules" / "workspace_phase2.js",
        dashboard / "modules" / "workspace_types.js",
        dashboard / "modules" / "http.js",
        dashboard / "modules" / "system_controls.js",
        dashboard / "modules" / "workspace_quality.js",
    )

    assert '/static/modules/workspace.js?v=34' in index
    assert '/static/app.js' not in index
    assert not (dashboard / "app.js").exists()
    assert source.count('request<WorkspaceHome>("/v1/workspace/home")') == budget[
        "workspace_home_request_count"
    ]
    assert "document.hidden" in source
    assert "window.setTimeout" in source
    assert "setInterval(refresh" not in source
    assert budget["workspace_idle_poll_seconds"] == 30
    assert budget["workspace_pause_when_hidden"] is True
    assert sum(path.stat().st_size for path in initial_files) <= budget[
        "workspace_initial_static_bytes"
    ]
