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
        image_engine_python=tmp_path / "image-engine" / "python.exe",
        image_engine_script=tmp_path / "image-engine" / "run.py",
        image_model_dir=tmp_path / "workspace" / "models" / "stable-diffusion-v1-5",
        image_output_dir=tmp_path / "workspace" / "generated_images",
        image_model_catalog_path=tmp_path / "config" / "image-models.json",
        image_backend="diffusers",
        forge_root=tmp_path / "workspace" / "forge",
        rembg_python=tmp_path / "workspace" / "rembg" / ".venv" / "Scripts" / "python.exe",
        rembg_script=tmp_path / "scripts" / "run_rembg.py",
        rembg_model_dir=tmp_path / "workspace" / "rembg" / "models",
        brain_provider="mock",
        network_enabled=False,
        supervisor_enabled=False,
        auto_start_kernel=False,
        heartbeat_seconds=0.2,
        task_poll_seconds=0.05,
        sleep_cycle_seconds=30,
    )


@pytest.fixture()
def application(settings: Settings) -> Application:
    return build_application(settings)
