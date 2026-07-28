from __future__ import annotations

import pytest
from pydantic import ValidationError

from eck.domain.enums import ComparisonOperator, EvidenceSource
from eck.domain.models import SuccessContract, VerificationCheck


def test_contract_requires_machine_check() -> None:
    with pytest.raises(ValidationError):
        SuccessContract(goal="Do a thing", checks=())


def test_contract_rejects_self_report_as_only_required_evidence() -> None:
    with pytest.raises(ValidationError):
        SuccessContract(
            goal="Do a thing",
            checks=(
                VerificationCheck(
                    name="done",
                    path="done",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
            ),
            required_evidence=(EvidenceSource.MODEL_SELF_REPORT,),
        )


def test_contract_is_immutable() -> None:
    contract = SuccessContract(
        goal="Reach a deterministic state",
        checks=(
            VerificationCheck(
                name="done",
                path="done",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
    )
    with pytest.raises(ValidationError):
        contract.goal = "Changed"  # type: ignore[misc]

