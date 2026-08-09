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
        export_dir=tmp_path / "workspace" / "exports",
        database_path=tmp_path / "data" / "eck.db",
        image_engine_python=tmp_path / "image-engine" / "python.exe",
        image_engine_script=tmp_path / "image-engine" / "run.py",
        image_model_dir=tmp_path / "workspace" / "models" / "stable-diffusion-v1-5",
        image_output_dir=tmp_path / "workspace" / "generated_images",
        video_output_dir=tmp_path / "workspace" / "generated_videos",
        video_backend="framepack",
        image_model_catalog_path=tmp_path / "config" / "image-models.json",
        image_backend="diffusers",
        forge_root=tmp_path / "workspace" / "forge",
        forge_stop_script=tmp_path / "scripts" / "stop-forge.ps1",
        rembg_python=tmp_path / "workspace" / "rembg" / ".venv" / "Scripts" / "python.exe",
        rembg_script=tmp_path / "scripts" / "run_rembg.py",
        rembg_model_dir=tmp_path / "workspace" / "rembg" / "models",
        video_engine_python=tmp_path / "workspace" / "framepack" / "python.exe",
        video_engine_script=tmp_path / "scripts" / "run_framepack_engine.py",
        framepack_source_dir=tmp_path / "workspace" / "framepack" / "source",
        cogvideo_python=tmp_path / "workspace" / "cogvideo" / ".conda" / "python.exe",
        cogvideo_script=tmp_path / "scripts" / "run_cogvideo_engine.py",
        cogvideo_model_dir=tmp_path / "workspace" / "cogvideo" / "model",
        cogvideo_smoke_report=tmp_path
        / "workspace"
        / "cogvideo"
        / "verified-runtime.json",
        brain_provider="mock",
        network_enabled=False,
        supervisor_enabled=False,
        autonomous_curriculum_enabled=False,
        autonomous_project_lab_enabled=False,
        skill_canary_delay_seconds=0,
        auto_start_kernel=False,
        heartbeat_seconds=0.2,
        task_poll_seconds=0.05,
        sleep_cycle_seconds=30,
    )


@pytest.fixture()
def application(settings: Settings) -> Application:
    return build_application(settings)
