from __future__ import annotations

from eck.capabilities.base import CapabilityDefinition
from eck.config import Settings
from eck.domain.enums import ComparisonOperator, RiskLevel
from eck.domain.models import ActionProposal, SuccessContract, VerificationCheck
from eck.policy.gate import PolicyGate


def contract() -> SuccessContract:
    return SuccessContract(
        goal="Produce a verified result",
        checks=(
            VerificationCheck(
                name="done",
                path="done",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
    )


def test_unknown_capability_is_blocked(settings: Settings) -> None:
    action = ActionProposal(capability="missing", operation="run")
    decision = PolicyGate(settings).evaluate(contract(), action, None)
    assert not decision.allowed
    assert decision.risk_level is RiskLevel.CRITICAL


def test_path_outside_workspace_is_blocked(settings: Settings) -> None:
    action = ActionProposal(
        capability="files.safe",
        operation="write",
        payload={"file_path": "C:/Windows/System32/drivers/etc/hosts"},
    )
    definition = CapabilityDefinition(
        name="files.safe",
        description="test",
        default_risk=RiskLevel.MEDIUM,
        deterministic=True,
    )
    decision = PolicyGate(settings).evaluate(contract(), action, definition)
    assert not decision.allowed
    assert "escapes" in decision.reasons[0]


def test_high_risk_requires_human_approval(settings: Settings) -> None:
    action = ActionProposal(
        capability="robot.move",
        operation="move",
        declared_risk=RiskLevel.HIGH,
        reversible=False,
    )
    definition = CapabilityDefinition(
        name="robot.move",
        description="test",
        default_risk=RiskLevel.HIGH,
        deterministic=False,
    )
    decision = PolicyGate(settings).evaluate(contract(), action, definition)
    assert decision.allowed
    assert decision.requires_approval


def test_system_file_mutation_is_absolute_v01_prohibition(settings: Settings) -> None:
    action = ActionProposal(capability="system.write", operation="write")
    definition = CapabilityDefinition(
        name="system.write",
        description="test",
        default_risk=RiskLevel.CRITICAL,
        deterministic=True,
        system_file_mutation=True,
    )
    decision = PolicyGate(settings).evaluate(contract(), action, definition)
    assert not decision.allowed
    assert not decision.requires_approval


def test_network_capability_is_blocked_when_network_is_disabled(settings: Settings) -> None:
    action = ActionProposal(capability="network.read", operation="get")
    definition = CapabilityDefinition(
        name="network.read",
        description="test",
        default_risk=RiskLevel.MEDIUM,
        deterministic=False,
        network_access=True,
    )
    decision = PolicyGate(settings).evaluate(contract(), action, definition)
    assert not decision.allowed
    assert "disabled" in decision.reasons[0]


def test_action_over_contract_cost_is_blocked(settings: Settings) -> None:
    action = ActionProposal(
        capability="compute",
        operation="run",
        estimated_cost_units=2000,
    )
    definition = CapabilityDefinition(
        name="compute",
        description="test",
        default_risk=RiskLevel.LOW,
        deterministic=True,
    )
    decision = PolicyGate(settings).evaluate(contract(), action, definition)
    assert not decision.allowed
    assert "cost" in decision.reasons[0]
