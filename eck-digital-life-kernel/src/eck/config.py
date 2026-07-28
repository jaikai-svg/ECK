from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

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
    sleep_cycle_seconds: float = Field(default=3600.0, ge=10, le=604800)
    task_poll_seconds: float = Field(default=0.5, ge=0.05, le=60)

    brain_provider: Literal["mock", "ollama"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None
    ollama_timeout_seconds: float = Field(default=120.0, ge=1, le=1800)

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

    @field_validator("data_dir", "workspace_dir", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator("ollama_model", mode="before")
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
        return self

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        assert self.database_path is not None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
