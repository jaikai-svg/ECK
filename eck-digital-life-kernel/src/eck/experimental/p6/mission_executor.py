from __future__ import annotations

import re

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.events.bus import EventBus
from eck.experimental.p6.artifacts import MissionArtifactSupportMixin
from eck.experimental.p6.deliberation import StructuredSoftwareDeliberation
from eck.experimental.p6.execution import MissionExecutionMixin
from eck.experimental.p6.mission_quality import MissionDevelopmentCouncil
from eck.experimental.p6.orchestration import MissionSoftwareOrchestrationMixin
from eck.experimental.p6.planning import MissionPlanningMixin
from eck.experimental.p6.software import MissionSoftwareArtifactMixin
from eck.services.missions import MissionService
from eck.services.project_lab import AutonomousProjectLabService
from eck.storage.sqlite import SQLiteStore


class DurableMissionExecutor(
    MissionPlanningMixin,
    MissionExecutionMixin,
    MissionSoftwareOrchestrationMixin,
    MissionSoftwareArtifactMixin,
    MissionArtifactSupportMixin,
):
    _executor_version = "p6-durable-react.v3"
    _low_resource_actions = frozenset(
        {
            "workspace.prepare",
            "learning.distill",
            "artifact.package",
            "github.publish",
            "mission.submit",
        }
    )
    _mission_id_pattern = re.compile(r"mission_[a-f0-9]{32}")
    _software_request = re.compile(
        r"(網站|網頁|web\s*site|website|landing\s*page|軟體|程式|專案|app|api)",
        re.I,
    )
    _website_request = re.compile(r"(網站|網頁|web\s*site|website|landing\s*page)", re.I)
    _allowed_site_suffixes = {".html", ".css", ".js", ".json", ".md", ".txt", ".svg"}

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        coder_brain: BrainProvider,
        project_lab: AutonomousProjectLabService,
        missions: MissionService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.coder_brain = coder_brain
        self.project_lab = project_lab
        self.missions = missions
        self.council = MissionDevelopmentCouncil(settings, store, coder_brain)
        self.deliberation = StructuredSoftwareDeliberation(
            coder_brain,
            max_rounds=settings.mission_deliberation_max_rounds,
        )
        assert settings.mission_workspace_dir is not None
        self.root = settings.mission_workspace_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
