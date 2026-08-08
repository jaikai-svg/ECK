from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta

from eck.capabilities.registry import CapabilityRegistry
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import (
    ApprovalStatus,
    EvidenceSource,
    TaskStatus,
    VerificationStatus,
)
from eck.domain.models import (
    CapabilityResult,
    Evidence,
    TaskCreate,
    TaskRecord,
)
from eck.events.bus import EventBus
from eck.memory.experience import ExperienceEngine
from eck.policy.gate import PolicyGate
from eck.storage.sqlite import SQLiteStore
from eck.verification.verifier import ContractVerifier


class TaskService:
    _transient_errors = {
        "connecterror",
        "connecttimeout",
        "httpError",
        "networkerror",
        "artifact rejected",
        "near-featureless",
        "no meaningful motion",
        "oserror",
        "readtimeout",
        "subprocesserror",
        "timeouterror",
        "workerunavailable",
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        registry: CapabilityRegistry,
        policy: PolicyGate,
        verifier: ContractVerifier,
        experiences: ExperienceEngine,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.registry = registry
        self.policy = policy
        self.verifier = verifier
        self.experiences = experiences

    async def submit(self, create: TaskCreate) -> TaskRecord:
        capability = self.registry.get(create.action.capability)
        definition = capability.definition if capability else None
        decision = self.policy.evaluate(create.success_contract, create.action, definition)
        idempotency_key = create.idempotency_key or self._submission_key(create)
        existing = self.store.find_active_task_by_idempotency_key(idempotency_key)
        if existing is not None:
            await self.events.publish(
                "TaskDuplicateSuppressed",
                existing.task_id,
                {
                    "idempotency_key": idempotency_key,
                    "capability": create.action.capability,
                },
                correlation_id=existing.task_id,
            )
            return existing
        task_id = new_id("task")
        task = self.store.create_task(
            task_id,
            create,
            decision.risk_level,
            idempotency_key=idempotency_key,
        )
        if task.task_id != task_id:
            await self.events.publish(
                "TaskDuplicateSuppressed",
                task.task_id,
                {
                    "idempotency_key": idempotency_key,
                    "capability": create.action.capability,
                    "race_resolved": True,
                },
                correlation_id=task.task_id,
            )
            return task
        await self.events.publish(
            "TaskSubmitted",
            task_id,
            {
                "goal": create.goal,
                "capability": create.action.capability,
                "risk_level": decision.risk_level.value,
            },
            correlation_id=task_id,
        )

        if not decision.allowed:
            task = self.store.update_task(task_id, status=TaskStatus.BLOCKED)
            await self.events.publish(
                "TaskBlocked",
                task_id,
                {"reasons": list(decision.reasons)},
                correlation_id=task_id,
            )
        elif decision.requires_approval:
            approval = self.store.create_approval(
                task_id, create.action, "; ".join(decision.reasons)
            )
            task = self.store.update_task(task_id, status=TaskStatus.WAITING_APPROVAL)
            await self.events.publish(
                "ApprovalRequested",
                approval.approval_id,
                {
                    "task_id": task_id,
                    "risk_level": decision.risk_level.value,
                    "reason": approval.reason,
                },
                correlation_id=task_id,
            )
        return task

    async def execute(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.status is TaskStatus.WAITING_APPROVAL:
            approval = self.store.get_task_approval(task_id)
            if approval is None or approval.status is ApprovalStatus.PENDING:
                return task
            if approval.status is ApprovalStatus.REJECTED:
                return self.store.update_task(task_id, status=TaskStatus.BLOCKED)
        if task.status not in {TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL}:
            return task

        capability = self.registry.get(task.action.capability)
        if capability is None:
            return self.store.update_task(task_id, status=TaskStatus.BLOCKED)

        attempts = task.attempts + 1
        task = self.store.update_task(
            task_id, status=TaskStatus.RUNNING, attempts=attempts
        )
        await self.events.publish(
            "TaskStarted",
            task_id,
            {"attempt": attempts, "capability": task.action.capability},
            correlation_id=task_id,
        )
        try:
            result = await capability.execute(task.action)
            repeated: CapabilityResult | None = None
            if task.success_contract.require_reproducible and capability.definition.deterministic:
                repeated = await capability.execute(task.action)
            report = self.verifier.verify(
                task.success_contract, result, repeated_result=repeated
            )
        except asyncio.CancelledError:
            await self._recover_interrupted_task(task, reason="execution_cancelled")
            raise
        except Exception as exc:  # safety boundary: capability failures become evidence
            now = utc_now()
            result = CapabilityResult(
                action_id=task.action.action_id,
                capability=task.action.capability,
                success=False,
                output={"error": type(exc).__name__, "detail": str(exc)},
                evidence=(
                    Evidence(
                        source=EvidenceSource.TOOL,
                        claim="Capability execution raised an exception.",
                        payload={"type": type(exc).__name__, "detail": str(exc)},
                    ),
                ),
                reversible=task.action.reversible,
                cost_units=0,
                started_at=now,
                finished_at=now,
            )
            report = self.verifier.verify(task.success_contract, result)

        if self._should_retry(task, result, report.status):
            return await self._schedule_retry(
                task,
                reason=self._error_signature(result) or report.reason,
                event_type="TaskRetryScheduled",
            )

        status_map = {
            VerificationStatus.VERIFIED_SUCCESS: TaskStatus.VERIFIED_SUCCESS,
            VerificationStatus.VERIFIED_FAILURE: TaskStatus.VERIFIED_FAILURE,
            VerificationStatus.UNVERIFIABLE: TaskStatus.UNVERIFIABLE,
            VerificationStatus.CONSTRAINT_VIOLATION: TaskStatus.CONSTRAINT_VIOLATION,
        }
        task = self.store.update_task(
            task_id,
            status=status_map[report.status],
            result=result,
            verification=report,
        )
        await self.events.publish(
            "TaskVerified",
            task_id,
            {
                "status": report.status.value,
                "score": report.score,
                "external_evidence": report.external_evidence_present,
                "reproducible": report.reproducible,
            },
            correlation_id=task_id,
        )
        if "no-learning" in task.labels:
            await self.events.publish(
                "NonLearningTaskCompleted",
                task_id,
                {"status": report.status.value, "labels": list(task.labels)},
                correlation_id=task_id,
            )
            return task
        experience, knowledge, reflection, skill = self.experiences.admit(task)
        await self.events.publish(
            "ExperienceRecorded",
            experience.experience_id,
            {
                "task_id": task_id,
                "admitted": experience.admitted,
                "reason": experience.admission_reason,
            },
            correlation_id=task_id,
        )
        await self.events.publish(
            "KnowledgeRecorded",
            knowledge.knowledge_id,
            {
                "task_id": task_id,
                "admitted": knowledge.admitted,
                "outcome": knowledge.outcome.value,
            },
            correlation_id=task_id,
        )
        await self.events.publish(
            "ReflectionRecorded",
            reflection.reflection_id,
            {
                "task_id": task_id,
                "generator": reflection.generator,
                "outcome": reflection.outcome.value,
            },
            correlation_id=task_id,
        )
        if skill:
            await self.events.publish(
                "SkillUpdated",
                skill.skill_id,
                {
                    "fingerprint": skill.fingerprint,
                    "success_count": skill.success_count,
                    "active": skill.active,
                },
                correlation_id=task_id,
            )
        return task

    async def recover_interrupted(self) -> list[TaskRecord]:
        recovered: list[TaskRecord] = []
        for task in self.store.list_tasks(statuses=(TaskStatus.RUNNING,), limit=500):
            recovered.append(
                await self._recover_interrupted_task(task, reason="kernel_restart")
            )
        return recovered

    async def _recover_interrupted_task(
        self, task: TaskRecord, *, reason: str
    ) -> TaskRecord:
        max_attempts = self._max_attempts(task)
        if task.action.reversible and task.attempts < max_attempts:
            recovered = await self._schedule_retry(
                task,
                reason=reason,
                event_type="TaskInterruptedRecovered",
            )
            return recovered
        if task.action.reversible:
            detail = f"{reason}; retry budget exhausted ({task.attempts}/{max_attempts})"
        else:
            detail = f"{reason}; irreversible interrupted task cannot be replayed"
        recovered = self.store.dead_letter_task(task.task_id, last_error=detail)
        await self.events.publish(
            "TaskDeadLettered",
            task.task_id,
            {
                "reason": reason,
                "previous_status": TaskStatus.RUNNING.value,
                "status": recovered.status.value,
                "reversible": task.action.reversible,
                "attempts": task.attempts,
                "max_attempts": max_attempts,
            },
            correlation_id=task.task_id,
        )
        return recovered

    async def handle_execution_timeout(self, task_id: str, timeout: float) -> TaskRecord:
        task = self.store.get_task(task_id)
        reason = f"Execution exceeded {timeout:.1f} seconds."
        if task.status is not TaskStatus.RUNNING:
            return task
        if task.action.reversible and task.attempts < self._max_attempts(task):
            return await self._schedule_retry(
                task,
                reason=reason,
                event_type="TaskTimedOutRetryScheduled",
            )
        dead = self.store.dead_letter_task(task_id, last_error=reason)
        await self.events.publish(
            "TaskDeadLettered",
            task_id,
            {
                "reason": reason,
                "attempts": task.attempts,
                "max_attempts": self._max_attempts(task),
            },
            correlation_id=task_id,
        )
        return dead

    async def handle_orchestrator_failure(
        self,
        task_id: str,
        exc: Exception,
    ) -> TaskRecord:
        task = self.store.get_task(task_id)
        reason = f"{type(exc).__name__}: {exc}"
        if task.action.reversible and task.attempts < self._max_attempts(task):
            return await self._schedule_retry(
                task,
                reason=reason,
                event_type="TaskOrchestratorRetryScheduled",
            )
        dead = self.store.dead_letter_task(task_id, last_error=reason)
        await self.events.publish(
            "TaskDeadLettered",
            task_id,
            {"reason": reason, "source": "orchestrator"},
            correlation_id=task_id,
        )
        return dead

    def execution_timeout(self, task: TaskRecord) -> float:
        capability = task.action.capability
        if capability == "video.generate":
            return self.settings.video_generation_timeout_seconds + 120
        if capability == "image.generate":
            return max(
                self.settings.image_generation_timeout_seconds + 60,
                self.settings.forge_startup_timeout_seconds + 60,
            )
        if capability == "runtime.skill":
            return self.settings.skill_worker_timeout_seconds + 60
        if capability == "web.critical_research":
            return max(600, self.settings.critical_research_timeout_seconds * 12)
        if capability == "academic.research":
            return max(300, self.settings.academic_research_timeout_seconds * 6)
        return self.settings.task_execution_timeout_seconds

    async def _schedule_retry(
        self,
        task: TaskRecord,
        *,
        reason: str,
        event_type: str,
    ) -> TaskRecord:
        delay = min(
            self.settings.task_retry_backoff_seconds * (2 ** max(0, task.attempts - 1)),
            self.settings.task_retry_backoff_max_seconds,
        )
        next_attempt_at = utc_now() + timedelta(seconds=delay)
        retried = self.store.schedule_task_retry(
            task.task_id,
            next_attempt_at=next_attempt_at,
            last_error=reason,
        )
        await self.events.publish(
            event_type,
            task.task_id,
            {
                "attempts": task.attempts,
                "max_attempts": self._max_attempts(task),
                "next_attempt_at": next_attempt_at.isoformat(),
                "reason": reason[:1000],
            },
            correlation_id=task.task_id,
        )
        return retried

    def _should_retry(
        self,
        task: TaskRecord,
        result: CapabilityResult,
        status: VerificationStatus,
    ) -> bool:
        if status is VerificationStatus.VERIFIED_SUCCESS:
            return False
        if not task.action.reversible or task.attempts >= self._max_attempts(task):
            return False
        signature = self._error_signature(result).casefold().replace("_", "")
        return any(error.casefold() in signature for error in self._transient_errors)

    def _max_attempts(self, task: TaskRecord) -> int:
        return min(self.settings.max_task_attempts, task.success_contract.max_attempts)

    @staticmethod
    def _error_signature(result: CapabilityResult) -> str:
        error_type = result.output.get("error_type") or result.output.get("error")
        detail = result.output.get("detail") or result.output.get("error_detail")
        return ": ".join(
            str(value).strip() for value in (error_type, detail) if str(value).strip()
        )

    @staticmethod
    def _submission_key(create: TaskCreate) -> str:
        material = {
            "goal": " ".join(create.goal.split()),
            "contract": create.success_contract.model_dump(
                mode="json",
                exclude={"contract_id", "created_at"},
            ),
            "action": create.action.model_dump(mode="json", exclude={"action_id"}),
            "labels": create.labels,
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"task:{hashlib.sha256(encoded).hexdigest()}"

    async def decide_approval(
        self, approval_id: str, decision: ApprovalStatus
    ) -> TaskRecord:
        approval = self.store.decide_approval(approval_id, decision)
        task_status = (
            TaskStatus.QUEUED
            if decision is ApprovalStatus.APPROVED
            else TaskStatus.BLOCKED
        )
        task = self.store.update_task(approval.task_id, status=task_status)
        await self.events.publish(
            "ApprovalDecided",
            approval_id,
            {"task_id": approval.task_id, "decision": decision.value},
            correlation_id=approval.task_id,
        )
        return task

    def next_queued(self, *, prefer_challenge: bool = False) -> TaskRecord | None:
        tasks = self.store.list_ready_tasks(limit=500)
        if not tasks:
            return None
        ordered = sorted(tasks, key=lambda item: item.created_at)
        urgent = next((item for item in ordered if "priority:urgent" in item.labels), None)
        if urgent:
            return urgent
        challenge = [item for item in ordered if "lane:challenge" in item.labels]
        autonomous = [item for item in ordered if "lane:challenge" not in item.labels]
        preferred = challenge if prefer_challenge else autonomous
        fallback = autonomous if prefer_challenge else challenge
        return (preferred or fallback)[0]

    def has_urgent_queued(self) -> bool:
        return any(
            "priority:urgent" in item.labels
            for item in self.store.list_ready_tasks(limit=500)
        )
