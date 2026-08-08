from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECK_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    identity: str = "eck-local"
    data_dir: Path = Path("data")
    workspace_dir: Path = Path("workspace")
    database_path: Path | None = None

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8420, ge=1024, le=65535)
    allow_remote_bind: bool = False
    auto_start_kernel: bool = True

    heartbeat_seconds: float = Field(default=5.0, ge=0.2, le=3600)
    heartbeat_event_seconds: float = Field(default=60.0, ge=1, le=86400)
    sleep_cycle_seconds: float = Field(default=3600.0, ge=10, le=604800)
    task_poll_seconds: float = Field(default=0.5, ge=0.05, le=60)

    brain_provider: Literal["mock", "ollama"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None
    ollama_timeout_seconds: float = Field(default=120.0, ge=1, le=1800)
    academic_research_timeout_seconds: float = Field(default=30.0, ge=5, le=120)
    academic_research_max_sources: int = Field(default=6, ge=3, le=12)
    academic_research_max_cycles: int = Field(default=3, ge=1, le=8)
    critical_research_enabled: bool = True
    critical_research_gdelt_base_url: str = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
    )
    critical_research_timeout_seconds: float = Field(default=30.0, ge=5, le=120)
    critical_research_max_sources: int = Field(default=8, ge=3, le=20)
    critical_research_max_claims: int = Field(default=5, ge=1, le=12)
    critical_research_max_document_chars: int = Field(
        default=50000, ge=2000, le=200000
    )
    critical_research_snapshot_retention_days: int = Field(default=30, ge=1, le=365)
    critical_research_default_timespan: str = Field(
        default="7d", pattern=r"^\d{1,3}(?:min|h|d|w|m)$"
    )
    critical_research_quality_window: int = Field(default=10, ge=5, le=100)
    critical_research_max_inconclusive_ratio: float = Field(default=0.5, ge=0, le=1)
    critical_research_near_duplicate_distance: int = Field(default=3, ge=0, le=16)
    image_generation_enabled: bool = True
    image_backend: Literal["diffusers", "forge"] = "forge"
    image_engine_python: Path = Path("workspace/image_engine/.venv/Scripts/python.exe")
    image_engine_script: Path = Path("scripts/run_image_engine.py")
    image_model_dir: Path = Path("workspace/models/stable-diffusion-v1-5")
    image_output_dir: Path = Path("workspace/generated_images")
    image_generation_timeout_seconds: float = Field(default=300.0, ge=30, le=1800)
    image_generation_steps: int = Field(default=36, ge=20, le=50)
    image_generation_guidance_scale: float = Field(default=7.5, ge=1, le=12)
    image_adult_content_enabled: bool = True
    image_adetailer_enabled: bool = True
    image_adetailer_model: str = "face_yolov8s.pt"
    image_model_catalog_path: Path = Path("config/image-models.json")
    forge_root: Path = Path("workspace/forge")
    forge_base_url: str = "http://127.0.0.1:7861"
    forge_checkpoint: str = "realisticVisionV60B1_v60B1VAE.safetensors"
    forge_auto_start: bool = True
    forge_start_script: Path = Path("scripts/start-forge.ps1")
    forge_startup_timeout_seconds: float = Field(default=900.0, ge=30, le=1800)
    rembg_enabled: bool = True
    rembg_python: Path = Path("workspace/rembg/.venv/Scripts/python.exe")
    rembg_script: Path = Path("scripts/run_rembg.py")
    rembg_model_dir: Path = Path("workspace/rembg/models")
    rembg_model: str = "birefnet-general"
    supervisor_enabled: bool = True
    supervisor_model: str | None = None
    supervisor_initial_delay_seconds: float = Field(default=30.0, ge=1, le=3600)
    supervisor_review_seconds: float = Field(default=600.0, ge=10, le=86400)
    supervisor_auto_assign: bool = True
    supervisor_max_reviews_per_day: int = Field(default=48, ge=1, le=1000)
    supervisor_max_output_tokens: int = Field(default=512, ge=64, le=4096)
    supervisor_context_window: int = Field(default=4096, ge=1024, le=131072)
    supervisor_num_gpu_layers: int | None = Field(default=12, ge=0, le=256)
    learning_stall_minutes: int = Field(default=30, ge=5, le=1440)

    skill_worker_enabled: bool = True
    skill_worker_image: str = "eck-skill-worker:0.1.0"
    skill_worker_timeout_seconds: float = Field(default=300.0, ge=10, le=3600)
    skill_worker_memory_mb: int = Field(default=1024, ge=256, le=16384)
    skill_dependency_install_enabled: bool = True
    skill_forge_auto_enable: bool = True
    autonomous_learning_percent: int = Field(default=90, ge=50, le=100)
    challenge_execution_percent: int = Field(default=10, ge=0, le=50)

    network_enabled: bool = False
    system_file_mutation_enabled: bool = False
    require_approval_for_medium_risk: bool = False
    max_task_attempts: int = Field(default=5, ge=1, le=100)
    max_events_page_size: int = Field(default=500, ge=10, le=5000)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = os.environ.get("ECK_CONFIG_FILE", "config/eck.yaml")
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path),
            file_secret_settings,
        )

    @field_validator(
        "data_dir",
        "workspace_dir",
        "image_engine_python",
        "image_engine_script",
        "image_model_dir",
        "image_output_dir",
        "image_model_catalog_path",
        "forge_root",
        "forge_start_script",
        "rembg_python",
        "rembg_script",
        "rembg_model_dir",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator("ollama_model", "supervisor_model", mode="before")
    @classmethod
    def empty_model_is_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_safety(self) -> Settings:
        if self.bind_host not in {"127.0.0.1", "localhost", "::1"} and not self.allow_remote_bind:
            raise ValueError(
                "Remote binding is disabled. Set ECK_ALLOW_REMOTE_BIND=true only behind "
                "an explicit local firewall or container port binding."
            )
        if self.database_path is None:
            self.database_path = self.data_dir / "eck.db"
        if self.autonomous_learning_percent + self.challenge_execution_percent != 100:
            raise ValueError("Autonomous learning and challenge percentages must total 100.")
        try:
            self.image_output_dir.resolve().relative_to(self.workspace_dir.resolve())
        except ValueError as exc:
            raise ValueError("Image output must stay inside the ECK workspace.") from exc
        try:
            self.forge_root.resolve().relative_to(self.workspace_dir.resolve())
            self.rembg_model_dir.resolve().relative_to(self.workspace_dir.resolve())
        except ValueError as exc:
            raise ValueError("Local image workers must stay inside the ECK workspace.") from exc
        forge_url = urlparse(self.forge_base_url)
        if forge_url.scheme != "http" or forge_url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Forge must use a local, non-billable HTTP endpoint.")
        discovery_url = urlparse(self.critical_research_gdelt_base_url)
        if discovery_url.scheme != "https" or discovery_url.hostname != (
            "api.gdeltproject.org"
        ):
            raise ValueError("Critical research discovery must use the free GDELT HTTPS API.")
        return self

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.rembg_model_dir.mkdir(parents=True, exist_ok=True)
        assert self.database_path is not None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
