from __future__ import annotations

import re
import subprocess as subprocess
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.events.bus import EventBus
from eck.runtime.worker import DockerSkillWorker
from eck.services.project_lab_components.drafting import ProjectLabDraftingMixin
from eck.services.project_lab_components.github import ProjectLabGitHubMixin
from eck.services.project_lab_components.lifecycle import ProjectLabLifecycleMixin
from eck.services.project_lab_components.support import ProjectLabSupportMixin
from eck.services.project_lab_components.validation import ProjectLabValidationMixin
from eck.storage.sqlite import SQLiteStore


class AutonomousProjectLabService(
    ProjectLabLifecycleMixin,
    ProjectLabGitHubMixin,
    ProjectLabDraftingMixin,
    ProjectLabValidationMixin,
    ProjectLabSupportMixin,
):
    _project_id_pattern = re.compile(r"project_[a-f0-9]{32}")
    _safe_name_pattern = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
    _secret_patterns = (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]{8,}"),
    )

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        coder_brain: BrainProvider,
        worker: DockerSkillWorker,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.coder_brain = coder_brain
        self.worker = worker
        self.root = settings.project_lab_dir
        self.state_path = settings.project_lab_state_path
        self._github_cache: dict[str, Any] | None = None
        self._github_checked_at = 0.0
        self.root.mkdir(parents=True, exist_ok=True)
