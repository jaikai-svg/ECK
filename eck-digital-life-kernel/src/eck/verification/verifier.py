from __future__ import annotations

from typing import Any

from eck.domain.enums import ComparisonOperator, VerificationStatus
from eck.domain.models import (
    CapabilityResult,
    SuccessContract,
    VerificationReport,
)


class ContractVerifier:
    def verify(
        self,
        contract: SuccessContract,
        result: CapabilityResult,
        *,
        repeated_result: CapabilityResult | None = None,
    ) -> VerificationReport:
        violated: list[str] = []
        for condition in contract.forbidden_conditions:
            actual = self._read_path(result.output, condition.path)
            if self._compare(actual, condition.operator, condition.expected):
                violated.append(condition.name)

        if violated:
            return VerificationReport(
                status=VerificationStatus.CONSTRAINT_VIOLATION,
                score=0,
                violated_constraints=tuple(violated),
                evidence_ids=tuple(e.evidence_id for e in result.evidence),
                external_evidence_present=any(e.source.is_external for e in result.evidence),
                reproducible=False,
                reason="A forbidden condition was observed.",
            )

        evidence_sources = {e.source for e in result.evidence}
        external_present = any(source.is_external for source in evidence_sources)
        missing_sources = [
            source.value for source in contract.required_evidence if source not in evidence_sources
        ]
        if not external_present or missing_sources:
            reason = "No external evidence was supplied."
            if missing_sources:
                reason = f"Missing required evidence: {', '.join(missing_sources)}."
            return VerificationReport(
                status=VerificationStatus.UNVERIFIABLE,
                score=0,
                evidence_ids=tuple(e.evidence_id for e in result.evidence),
                external_evidence_present=external_present,
                reproducible=False,
                reason=reason,
            )

        passed: list[str] = []
        failed: list[str] = []
        passed_weight = 0.0
        total_weight = sum(check.weight for check in contract.checks)
        for check in contract.checks:
            actual = self._read_path(result.output, check.path)
            if self._compare(actual, check.operator, check.expected):
                passed.append(check.name)
                passed_weight += check.weight
            else:
                failed.append(check.name)
        score = passed_weight / total_weight if total_weight else 0.0

        reproducible = not contract.require_reproducible
        if contract.require_reproducible and repeated_result is not None:
            reproducible = self._stable_projection(result) == self._stable_projection(
                repeated_result
            )

        success = (
            result.success
            and score >= contract.minimum_score
            and not failed
            and reproducible
        )
        if success:
            status = VerificationStatus.VERIFIED_SUCCESS
            reason = (
                "All contract checks passed with external, reproducible evidence."
                if contract.require_reproducible
                else "All contract checks passed with required external evidence."
            )
        elif contract.require_reproducible and repeated_result is None:
            status = VerificationStatus.UNVERIFIABLE
            reason = "The contract requires reproduction, but no repeated result was supplied."
        else:
            status = VerificationStatus.VERIFIED_FAILURE
            if failed:
                reason = f"Contract checks failed: {', '.join(failed)}."
            elif not result.success:
                reason = str(
                    result.output.get("error")
                    or result.output.get("detail")
                    or "The capability reported failure."
                )
            else:
                reason = "The outcome was not reproducible."

        return VerificationReport(
            status=status,
            score=score,
            passed_checks=tuple(passed),
            failed_checks=tuple(failed),
            evidence_ids=tuple(e.evidence_id for e in result.evidence),
            external_evidence_present=external_present,
            reproducible=reproducible,
            reason=reason,
        )

    @staticmethod
    def _read_path(value: dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @staticmethod
    def _compare(actual: Any, operator: ComparisonOperator, expected: Any) -> bool:
        try:
            if operator is ComparisonOperator.EQ:
                return bool(actual == expected)
            if operator is ComparisonOperator.NE:
                return bool(actual != expected)
            if operator is ComparisonOperator.GT:
                return bool(actual > expected)
            if operator is ComparisonOperator.GTE:
                return bool(actual >= expected)
            if operator is ComparisonOperator.LT:
                return bool(actual < expected)
            if operator is ComparisonOperator.LTE:
                return bool(actual <= expected)
            if operator is ComparisonOperator.CONTAINS:
                return expected in actual
            if operator is ComparisonOperator.TRUTHY:
                return bool(actual)
        except (TypeError, ValueError):
            return False
        return False

    @staticmethod
    def _stable_projection(result: CapabilityResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "output": result.output,
            "evidence": [
                {
                    "source": evidence.source.value,
                    "claim": evidence.claim,
                    "payload": evidence.payload,
                }
                for evidence in result.evidence
            ],
            "reversible": result.reversible,
            "cost_units": result.cost_units,
        }
