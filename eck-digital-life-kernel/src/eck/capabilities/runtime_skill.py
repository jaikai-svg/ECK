from __future__ import annotations

import hashlib
import json

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence
from eck.runtime.worker import DockerSkillWorker
from eck.storage.sqlite import SQLiteStore


class RuntimeSkillCapability(Capability):
    definition = CapabilityDefinition(
        name="runtime.skill",
        description="Execute a versioned, tested skill in an isolated Docker worker.",
        default_risk=RiskLevel.MEDIUM,
        deterministic=False,
    )

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        worker: DockerSkillWorker,
    ) -> None:
        self.settings = settings
        self.store = store
        self.worker = worker

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        name = str(action.payload.get("skill_name", ""))
        operation = str(action.payload.get("operation", ""))
        payload = action.payload.get("input", {})
        skill = self.store.find_active_runtime_skill(name)
        usage_id = ""
        if skill is None:
            output = {"success": False, "error": f"No active runtime skill: {name}"}
        elif "network:public" in skill.manifest.permissions and not self.settings.network_enabled:
            output = {"success": False, "error": "Network access is disabled."}
        else:
            worker_input = payload if isinstance(payload, dict) else {}
            task_id = str(action.payload.get("_task_id", ""))
            if task_id:
                encoded = json.dumps(
                    worker_input,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
                usage = self.store.create_task_skill_usage(
                    {
                        "task_id": task_id,
                        "project_id": action.payload.get("project_id"),
                        "runtime_skill_id": skill.runtime_skill_id,
                        "skill_name": skill.manifest.name,
                        "skill_version": skill.manifest.version,
                        "operation": operation,
                        "attempt": int(action.payload.get("_task_attempt", 1)),
                        "input_summary": self._summary(worker_input),
                        "input_sha256": hashlib.sha256(encoded).hexdigest(),
                    }
                )
                usage_id = str(usage["usage_id"])
            try:
                output = await self.worker.execute(skill, operation, worker_input)
            except Exception as exc:
                output = {
                    "success": False,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                }
            if usage_id:
                output = {**output, "_task_skill_usage_id": usage_id}
        success = bool(output.get("success"))
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=success,
            output=output,
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="The isolated Docker skill worker returned a structured result.",
                    payload={"skill_name": name, "operation": operation, "success": success},
                ),
            ),
            reversible=action.reversible,
            cost_units=10,
            started_at=started,
            finished_at=utc_now(),
        )

    @staticmethod
    def _summary(value: dict[str, object]) -> str:
        summary = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return summary if len(summary) <= 500 else summary[:497] + "..."
