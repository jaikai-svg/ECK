from __future__ import annotations

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
        if skill is None:
            output = {"success": False, "error": f"No active runtime skill: {name}"}
        elif "network:public" in skill.manifest.permissions and not self.settings.network_enabled:
            output = {"success": False, "error": "Network access is disabled."}
        else:
            output = await self.worker.execute(
                skill,
                operation,
                payload if isinstance(payload, dict) else {},
            )
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
