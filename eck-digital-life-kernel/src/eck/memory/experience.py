from __future__ import annotations

from eck.domain.enums import VerificationStatus
from eck.domain.models import (
    ExperienceRecord,
    KnowledgeRecord,
    ReflectionRecord,
    SkillRecord,
    TaskRecord,
)
from eck.storage.sqlite import SQLiteStore


class ExperienceEngine:
    """Stores every outcome but only promotes externally verified successes."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def admit(
        self, task: TaskRecord
    ) -> tuple[
        ExperienceRecord,
        KnowledgeRecord,
        ReflectionRecord,
        SkillRecord | None,
    ]:
        if task.verification is None:
            raise ValueError("A task cannot enter experience admission without verification.")

        report = task.verification
        admitted = (
            report.status is VerificationStatus.VERIFIED_SUCCESS
            and report.external_evidence_present
            and report.reproducible
        )
        if admitted:
            reason = "Externally verified and reproduced; eligible for skill crystallization."
        elif report.status is VerificationStatus.UNVERIFIABLE:
            reason = "Retained as an unverified trace; excluded from skill learning."
        elif report.status is VerificationStatus.CONSTRAINT_VIOLATION:
            reason = "Retained as a safety counterexample; excluded from skill learning."
        else:
            reason = "Retained as a verified failure; excluded from positive skill learning."

        experience = self.store.add_experience(
            task_id=task.task_id,
            capability=task.action.capability,
            outcome=report.status,
            summary=f"{task.goal}: {report.reason}",
            evidence_ids=report.evidence_ids,
            admitted=admitted,
            admission_reason=reason,
        )

        knowledge = self.store.add_knowledge(
            task_id=task.task_id,
            capability=task.action.capability,
            claim=(
                f"Task outcome {report.status.value} for goal {task.goal!r}; "
                f"verifier reason: {report.reason}"
            ),
            outcome=report.status,
            evidence_ids=report.evidence_ids,
            externally_grounded=report.external_evidence_present,
            reproducible=report.reproducible,
            admitted=report.external_evidence_present,
        )

        reflection_templates = {
            VerificationStatus.VERIFIED_SUCCESS: (
                "The fixed success contract was satisfied.",
                "Positive reuse is permitted only from external evidence and reproduction.",
                "Retain the verified procedure; activate it only after the skill threshold.",
            ),
            VerificationStatus.VERIFIED_FAILURE: (
                "At least one success check failed.",
                "A failed check is a counterexample, not positive learning.",
                "Revise the proposal or contract candidate, then run a new bounded task.",
            ),
            VerificationStatus.UNVERIFIABLE: (
                "The result lacked sufficient trusted evidence.",
                "Unverified output must not be promoted as knowledge or skill.",
                "Acquire an approved external evidence source before retrying.",
            ),
            VerificationStatus.CONSTRAINT_VIOLATION: (
                "A forbidden condition was detected.",
                "Safety constraints override score and apparent task success.",
                "Stop this route and require a safer proposal or human review.",
            ),
        }
        observation, lesson, next_step = reflection_templates[report.status]
        reflection = self.store.add_reflection(
            task_id=task.task_id,
            capability=task.action.capability,
            outcome=report.status,
            observation=observation,
            lesson=lesson,
            next_step=next_step,
            verification_report_id=report.report_id,
            evidence_ids=report.evidence_ids,
        )

        skill: SkillRecord | None = None
        if admitted and task.result:
            output = task.result.output
            fingerprint = output.get("skill_fingerprint")
            procedure = output.get("skill_procedure")
            if isinstance(fingerprint, str) and isinstance(procedure, dict):
                skill = self.store.upsert_skill_success(
                    fingerprint=fingerprint,
                    name=str(output.get("skill_name", fingerprint)),
                    capability=task.action.capability,
                    procedure=procedure,
                    verification_basis=report.model_dump(mode="json"),
                )
        return experience, knowledge, reflection, skill
