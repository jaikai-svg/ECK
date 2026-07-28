from __future__ import annotations

from eck.domain.enums import ComparisonOperator, RiskLevel, VerificationStatus
from eck.domain.models import (
    ActionProposal,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
    VerificationReport,
)
from eck.memory.experience import ExperienceEngine


def test_unverifiable_outcome_is_recorded_but_not_admitted_as_knowledge(
    application,
) -> None:
    create = TaskCreate(
        goal="Evaluate a claim without external evidence",
        success_contract=SuccessContract(
            goal="Evaluate a claim without external evidence",
            checks=(
                VerificationCheck(
                    name="done",
                    path="done",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
            ),
        ),
        action=ActionProposal(
            capability="safe_expression",
            operation="evaluate",
            payload={"expression": "x + 1", "cases": [{"x": 1, "expected": 2}]},
        ),
    )
    task = application.store.create_task("task_memory_test", create, RiskLevel.LOW)
    task = application.store.update_task(
        task.task_id,
        verification=VerificationReport(
            status=VerificationStatus.UNVERIFIABLE,
            score=1,
            external_evidence_present=False,
            reproducible=False,
            reason="Only a model self-report was available.",
        ),
    )

    _, knowledge, reflection, skill = ExperienceEngine(application.store).admit(task)

    assert not knowledge.admitted
    assert not knowledge.externally_grounded
    assert reflection.outcome is VerificationStatus.UNVERIFIABLE
    assert reflection.generator == "deterministic-template.v1"
    assert skill is None
