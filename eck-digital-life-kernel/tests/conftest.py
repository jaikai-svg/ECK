from __future__ import annotations

from pathlib import Path

import pytest

from eck.app import Application, build_application
from eck.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        identity="eck-test",
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        database_path=tmp_path / "data" / "eck.db",
        brain_provider="mock",
        auto_start_kernel=False,
        heartbeat_seconds=0.2,
        task_poll_seconds=0.05,
        sleep_cycle_seconds=30,
    )


@pytest.fixture()
def application(settings: Settings) -> Application:
    return build_application(settings)

