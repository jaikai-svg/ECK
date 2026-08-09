from __future__ import annotations

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence
from eck.services.self_model import RepositorySelfModelService


class SelfInspectCapability(Capability):
    definition = CapabilityDefinition(
        name="core.self_inspect",
        description="Inspect ECK repository structure, symbols, dependencies, and source hashes.",
        default_risk=RiskLevel.LOW,
        deterministic=True,
        autonomous_safe=True,
    )

    def __init__(self, self_model: RepositorySelfModelService) -> None:
        self.self_model = self_model

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        try:
            if action.operation == "refresh":
                model = self.self_model.refresh()
                output = {
                    "summary": model["summary"],
                    "architecture": model["architecture"],
                    "source_tree_sha256": model["source_tree_sha256"],
                    "metrics": {"completed": True},
                }
            elif action.operation in {"query", "search"}:
                output = self.self_model.query(
                    str(action.payload.get("query", "")),
                    limit=min(50, max(1, int(action.payload.get("limit", 20)))),
                )
                output["metrics"] = {"completed": True}
            elif action.operation == "status":
                model = self.self_model.ensure()
                output = {
                    "summary": model["summary"],
                    "architecture": model["architecture"],
                    "boundaries": model["boundaries"],
                    "source_tree_sha256": model["source_tree_sha256"],
                    "metrics": {"completed": True},
                }
            else:
                raise ValueError(f"Unsupported self-inspection operation: {action.operation}")
            success = True
            claim = "ECK inspected its local source tree and persisted a hashed repository map."
        except (OSError, ValueError, TypeError) as exc:
            success = False
            output = {"error": str(exc), "metrics": {"completed": False}}
            claim = "Repository self-inspection failed."
        finished = utc_now()
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=success,
            output=output,
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim=claim,
                    payload={"model_path": str(self.self_model.model_path)},
                ),
            ),
            reversible=True,
            cost_units=1,
            started_at=started,
            finished_at=finished,
        )
