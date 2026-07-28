from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.domain.enums import (
    ApprovalStatus,
    ComparisonOperator,
    EvidenceSource,
    RiskLevel,
    TaskStatus,
)
from eck.domain.models import (
    ActionProposal,
    CapabilityResult,
    Evidence,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
)


class HighRiskCapability(Capability):
    definition = CapabilityDefinition(
        name="test.high_risk",
        description="Test-only high-risk capability.",
        default_risk=RiskLevel.HIGH,
        deterministic=True,
    )

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        now = datetime.now(UTC)
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=True,
            output={"done": True},
            evidence=(Evidence(source=EvidenceSource.HUMAN, claim="approved test"),),
            started_at=now,
            finished_at=now,
        )


def create_request() -> TaskCreate:
    contract = SuccessContract(
        goal="Run an explicitly approved test action",
        checks=(
            VerificationCheck(
                name="done",
                path="done",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
        required_evidence=(EvidenceSource.HUMAN,),
    )
    return TaskCreate(
        goal=contract.goal,
        success_contract=contract,
        action=ActionProposal(
            capability="test.high_risk",
            operation="run",
            declared_risk=RiskLevel.HIGH,
        ),
    )


@pytest.mark.asyncio
async def test_high_risk_task_waits_for_and_accepts_approval(application) -> None:
    application.registry.register(HighRiskCapability())
    task = await application.tasks.submit(create_request())
    assert task.status is TaskStatus.WAITING_APPROVAL
    approval = application.store.get_task_approval(task.task_id)
    assert approval is not None

    task = await application.tasks.decide_approval(
        approval.approval_id, ApprovalStatus.APPROVED
    )
    assert task.status is TaskStatus.QUEUED
    task = await application.tasks.execute(task.task_id)
    assert task.status is TaskStatus.VERIFIED_SUCCESS


@pytest.mark.asyncio
async def test_rejected_approval_blocks_task(application) -> None:
    application.registry.register(HighRiskCapability())
    task = await application.tasks.submit(create_request())
    approval = application.store.get_task_approval(task.task_id)
    assert approval is not None
    task = await application.tasks.decide_approval(
        approval.approval_id, ApprovalStatus.REJECTED
    )
    assert task.status is TaskStatus.BLOCKED

