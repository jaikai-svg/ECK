from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from eck.capabilities.base import CapabilityDefinition
from eck.config import Settings
from eck.domain.enums import RiskLevel
from eck.domain.models import ActionProposal, PolicyDecision, SuccessContract


class PolicyGate:
    """Enforces the graded 'otherwise do nothing' rule before execution."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        contract: SuccessContract,
        action: ActionProposal,
        definition: CapabilityDefinition | None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        risk = action.declared_risk

        if definition is None:
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                risk_level=RiskLevel.CRITICAL,
                reasons=("Capability is not registered.",),
            )

        if not contract.checks:
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                risk_level=RiskLevel.CRITICAL,
                reasons=("No machine-checkable success condition was supplied.",),
            )

        if action.estimated_cost_units > contract.max_cost_units:
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                risk_level=RiskLevel.HIGH,
                reasons=("Estimated cost exceeds the Success Contract limit.",),
            )

        risk = max((risk, definition.default_risk), key=lambda item: item.rank)

        if definition.network_access:
            risk = max((risk, RiskLevel.HIGH), key=lambda item: item.rank)
            if not self.settings.network_enabled:
                return PolicyDecision(
                    allowed=False,
                    requires_approval=False,
                    risk_level=risk,
                    reasons=("Network capabilities are disabled by configuration.",),
                )

        if definition.system_file_mutation:
            risk = RiskLevel.CRITICAL
            if not self.settings.system_file_mutation_enabled:
                return PolicyDecision(
                    allowed=False,
                    requires_approval=False,
                    risk_level=risk,
                    reasons=("System-file mutation is an absolute v0.1 prohibition.",),
                )

        path_violation = self._find_path_outside_workspace(action.payload)
        if path_violation:
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                risk_level=RiskLevel.CRITICAL,
                reasons=(f"Path escapes the ECK workspace: {path_violation}",),
            )

        if contract.reversible_exploration_only and not action.reversible:
            risk = max((risk, RiskLevel.HIGH), key=lambda item: item.rank)
            reasons.append(
                "The proposal is irreversible while the contract permits only exploration."
            )

        requires_approval = risk.rank >= RiskLevel.HIGH.rank or (
            risk is RiskLevel.MEDIUM and self.settings.require_approval_for_medium_risk
        )
        if requires_approval:
            reasons.append("Human approval is required for this risk level.")

        return PolicyDecision(
            allowed=True,
            requires_approval=requires_approval,
            risk_level=risk,
            reasons=tuple(reasons) or ("Policy checks passed.",),
        )

    def _find_path_outside_workspace(self, payload: dict[str, Any]) -> str | None:
        workspace = self.settings.workspace_dir.resolve()

        def walk(value: Any, key: str = "") -> str | None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    found = walk(child, child_key.lower())
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = walk(child, key)
                    if found:
                        return found
            elif isinstance(value, str) and ("path" in key or "file" in key):
                if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
                    return value
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = workspace / candidate
                try:
                    candidate.resolve().relative_to(workspace)
                except ValueError:
                    return str(candidate)
            return None

        return walk(payload)
