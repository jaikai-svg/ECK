from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.events.bus import EventBus
from eck.runtime.worker import DockerSkillWorker
from eck.storage.sqlite import SQLiteStore


class ProjectLabMixinBase:
    _project_id_pattern: re.Pattern[str]
    _safe_name_pattern: re.Pattern[str]
    _secret_patterns: tuple[re.Pattern[str], ...]
    settings: Settings
    store: SQLiteStore
    events: EventBus
    coder_brain: BrainProvider
    worker: DockerSkillWorker
    root: Path
    state_path: Path
    _github_cache: dict[str, Any] | None
    _github_checked_at: float

    def get_project(self, project_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        raise NotImplementedError

    def _eligible_research(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def _record_cycle(
        self,
        status: str,
        detail: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _publish_deferred(
        self,
        manifest: dict[str, Any],
        detail: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _validate_in_docker(self, source_dir: Path) -> dict[str, Any]:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
