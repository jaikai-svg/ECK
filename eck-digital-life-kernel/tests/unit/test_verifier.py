from __future__ import annotations

from datetime import UTC, datetime

from eck.domain.enums import (
    ComparisonOperator,
    EvidenceSource,
    VerificationStatus,
)
from eck.domain.models import (
    CapabilityResult,
    Evidence,
    ForbiddenCondition,
    SuccessContract,
    VerificationCheck,
)
from eck.verification.verifier import ContractVerifier


def result(*, output: dict, source: EvidenceSource) -> CapabilityResult:
    now = datetime.now(UTC)
    return CapabilityResult(
        action_id="action_test",
        capability="test",
        success=True,
        output=output,
        evidence=(Evidence(source=source, claim="observed"),),
        started_at=now,
        finished_at=now,
    )


def contract() -> SuccessContract:
    return SuccessContract(
        goal="Complete a verified task",
        checks=(
            VerificationCheck(
                name="done",
                path="done",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
        required_evidence=(EvidenceSource.UNIT_TEST,),
        require_reproducible=True,
    )


def test_verifies_external_reproducible_result() -> None:
    first = result(output={"done": True}, source=EvidenceSource.UNIT_TEST)
    second = result(output={"done": True}, source=EvidenceSource.UNIT_TEST)
    report = ContractVerifier().verify(contract(), first, repeated_result=second)
    assert report.status is VerificationStatus.VERIFIED_SUCCESS
    assert report.external_evidence_present
    assert report.reproducible


def test_self_report_cannot_verify_success() -> None:
    item = result(output={"done": True}, source=EvidenceSource.MODEL_SELF_REPORT)
    permissive = SuccessContract(
        goal="Complete task",
        checks=contract().checks,
        require_reproducible=False,
    )
    report = ContractVerifier().verify(permissive, item)
    assert report.status is VerificationStatus.UNVERIFIABLE


def test_unstable_repeated_result_fails() -> None:
    first = result(output={"done": True}, source=EvidenceSource.UNIT_TEST)
    second = result(output={"done": False}, source=EvidenceSource.UNIT_TEST)
    report = ContractVerifier().verify(contract(), first, repeated_result=second)
    assert report.status is VerificationStatus.VERIFIED_FAILURE
    assert not report.reproducible


def test_forbidden_condition_wins_over_success() -> None:
    guarded = SuccessContract(
        goal="Complete task without leakage",
        checks=contract().checks,
        forbidden_conditions=(
            ForbiddenCondition(
                name="future leakage",
                path="leakage",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
        required_evidence=(EvidenceSource.UNIT_TEST,),
    )
    item = result(
        output={"done": True, "leakage": True}, source=EvidenceSource.UNIT_TEST
    )
    report = ContractVerifier().verify(guarded, item, repeated_result=item)
    assert report.status is VerificationStatus.CONSTRAINT_VIOLATION


def test_nonreproducible_contract_reports_failed_check_precisely() -> None:
    media_contract = SuccessContract(
        goal="Generate an image with the requested dimensions",
        checks=(
            VerificationCheck(
                name="height matches request",
                path="metadata.height",
                operator=ComparisonOperator.EQ,
                expected=896,
            ),
        ),
        required_evidence=(EvidenceSource.UNIT_TEST,),
        require_reproducible=False,
    )
    item = result(output={"metadata": {"height": 768}}, source=EvidenceSource.UNIT_TEST)

    report = ContractVerifier().verify(media_contract, item)

    assert report.status is VerificationStatus.VERIFIED_FAILURE
    assert report.reason == "Contract checks failed: height matches request."
    assert report.reproducible is True
