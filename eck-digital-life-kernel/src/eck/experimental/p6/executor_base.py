from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.domain.models import MissionRecord, MissionStepRecord
from eck.events.bus import EventBus
from eck.experimental.p6.deliberation import StructuredSoftwareDeliberation
from eck.experimental.p6.mission_quality import MissionDevelopmentCouncil
from eck.services.missions import MissionService
from eck.services.project_lab import AutonomousProjectLabService
from eck.storage.sqlite import SQLiteStore


@dataclass(slots=True)
class StepOutcome:
    success: bool
    output: dict[str, Any]
    error: str = ""
    retryable: bool = False
    correction: str = ""


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.tags: set[str] = set()
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.add(tag.casefold())
        self._in_title = tag.casefold() == "title"
        for name, value in attrs:
            if name.casefold() in {"href", "src"} and value:
                self.references.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


class MissionExecutorMixinBase:
    settings: Settings
    store: SQLiteStore
    events: EventBus
    coder_brain: BrainProvider
    project_lab: AutonomousProjectLabService
    missions: MissionService
    council: MissionDevelopmentCouncil
    deliberation: StructuredSoftwareDeliberation
    root: Path

    def _safe_relative_path(self, value: str) -> str:
        raise NotImplementedError

    def _source_dir(self, mission_id: str) -> Path:
        raise NotImplementedError

    def _mission_dir(self, mission_id: str) -> Path:
        raise NotImplementedError

    def _step_by_key(self, mission_id: str, step_key: str) -> MissionStepRecord:
        raise NotImplementedError

    async def _implement_python(
        self,
        mission: MissionRecord,
        spec: dict[str, Any],
    ) -> StepOutcome:
        raise NotImplementedError

    async def _prepare_workspace(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _research_references(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _specify_software(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _design_architecture(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _plan_architecture(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _implement_software(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _execute_architect_microtask(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _enhance_software(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _review_quality(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _improve_quality(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _validate_software(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _distill_learning(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _package_artifact(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _publish_github(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    async def _submit_mission(
        self, mission: MissionRecord, step: MissionStepRecord
    ) -> StepOutcome:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
