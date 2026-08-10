from __future__ import annotations

from eck.capabilities.names import capability_equivalent
from eck.capabilities.registry import CapabilityRegistry
from eck.core.time import utc_now
from eck.domain.enums import (
    ChallengeStatus,
    ComparisonOperator,
    EvidenceSource,
    MissionStatus,
    RiskLevel,
    TaskStatus,
)
from eck.domain.models import (
    ActionProposal,
    EventRecord,
    MissionCompletionCreate,
    MissionCreate,
    MissionRecord,
    MissionReviewDecision,
    MissionUpdate,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
)
from eck.events.bus import EventBus
from eck.services.tasks import TaskService
from eck.services.versioning import VersionService
from eck.storage.sqlite import SQLiteStore


class MissionService:
    def __init__(
        self,
        store: SQLiteStore,
        events: EventBus,
        versions: VersionService,
        tasks: TaskService,
        registry: CapabilityRegistry,
    ) -> None:
        self.store = store
        self.events = events
        self.versions = versions
        self.tasks = tasks
        self.registry = registry
        self._import_legacy_challenges()

    async def create(self, create: MissionCreate) -> MissionRecord:
        mission = self.store.create_mission(create)
        await self.events.publish(
            "MissionCreated",
            mission.mission_id,
            {
                "source": mission.source,
                "schedule": mission.schedule,
                "priority": mission.priority,
            },
        )
        task = await self.tasks.submit(self._planning_task(mission))
        current_progress = self.store.get_mission(mission.mission_id).progress
        return self.store.set_mission_status(
            mission.mission_id,
            MissionStatus.PREPARING,
            progress={
                **current_progress,
                "completion_percent": 0,
                "current_step": "正在建立可驗證計畫與持久化執行步驟",
                "planning_task_id": task.task_id,
                "execution_kind": current_progress.get("execution_kind", "auto"),
            },
        )

    async def handle_task_verified(self, event: EventRecord) -> None:
        task = self.store.get_task(event.aggregate_id)
        mission_label = next(
            (label for label in task.labels if label.startswith("mission:")),
            None,
        )
        if mission_label is None or task.action.capability != "task.plan":
            return
        mission_id = mission_label.split(":", 1)[1]
        mission = self.store.get_mission(mission_id)
        if mission.status in {
            MissionStatus.APPROVED,
            MissionStatus.CANCELLED,
            MissionStatus.AWAITING_REVIEW,
        }:
            return
        output = task.result.output if task.result else {}
        required = [
            str(item)
            for item in output.get("required_capabilities", [])
            if str(item).strip()
        ]
        native = {str(item["name"]) for item in self.registry.list()}
        runtime = {
            item.manifest.name
            for item in self.store.list_runtime_skills(limit=10000)
            if item.status.value == "active"
        }
        available = native | runtime
        missing = [
            item
            for item in required
            if not any(capability_equivalent(item, candidate) for candidate in available)
        ]
        execution_kind = str(mission.progress.get("execution_kind", "auto"))
        if execution_kind == "software_project":
            missing = []
        succeeded = task.status is TaskStatus.VERIFIED_SUCCESS
        if not succeeded:
            current_step = "規劃未通過驗證；監督者會重新研究需求，不影響自主學習。"
        elif missing:
            current_step = f"發現能力缺口：{', '.join(missing[:5])}；轉交監督者研究與鍛造。"
        else:
            current_step = "必要能力已存在；課題留在 10% 通道等待逐步執行與外部證據。"
        updated = self.store.set_mission_status(
            mission_id,
            MissionStatus.PREPARING if missing else MissionStatus.ACTIVE,
            progress={
                "completion_percent": 5 if succeeded else 0,
                "current_step": current_step,
                "planning_task_id": task.task_id,
                "plan": output,
                "required_capabilities": required,
                "missing_capabilities": missing,
                "execution_kind": execution_kind,
            },
        )
        await self.events.publish(
            "MissionPlanUpdated",
            mission_id,
            {
                "status": updated.status.value,
                "missing_capabilities": missing,
                "planning_task_id": task.task_id,
            },
            correlation_id=mission_id,
        )

    async def update(self, mission_id: str, update: MissionUpdate) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission.status in {MissionStatus.APPROVED, MissionStatus.CANCELLED}:
            raise ValueError("Approved or cancelled missions are immutable.")
        updated = self.store.update_mission(mission_id, update)
        await self.events.publish(
            "MissionUpdated",
            mission_id,
            {"fields": sorted(update.model_fields_set)},
            correlation_id=mission_id,
        )
        return updated

    async def submit_completion(
        self,
        mission_id: str,
        create: MissionCompletionCreate,
    ) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission.status in {MissionStatus.APPROVED, MissionStatus.CANCELLED}:
            raise ValueError("The mission is already closed.")
        if mission.progress.get("execution_kind") == "software_project":
            steps = self.store.list_mission_steps(mission_id)
            review_steps = [item for item in steps if item.action_kind == "quality.review"]
            if review_steps and any(
                item.status.value != "succeeded" for item in review_steps
            ):
                raise ValueError(
                    "Software missions require all independent expert reviews before submission."
                )
            validations = [
                item
                for item in steps
                if item.action_kind == "software.validate" and item.status.value == "succeeded"
            ]
            if review_steps and not validations:
                raise ValueError("Software mission validation evidence is incomplete.")
        updated = self.store.set_mission_status(
            mission_id,
            MissionStatus.AWAITING_REVIEW,
            progress={
                **mission.progress,
                "completion_percent": 100,
                "current_step": "等待建立者驗收",
            },
            result_summary=create.result_summary,
            evidence=create.evidence,
            submitted_at=utc_now(),
        )
        await self.events.publish(
            "MissionSubmittedForReview",
            mission_id,
            {"evidence_count": len(create.evidence)},
            correlation_id=mission_id,
        )
        return updated

    async def review(
        self,
        mission_id: str,
        decision: MissionReviewDecision,
    ) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission.status is not MissionStatus.AWAITING_REVIEW:
            raise ValueError("Only a submitted mission can be reviewed.")
        status = MissionStatus.APPROVED if decision.approved else MissionStatus.REJECTED
        progress = {
            **mission.progress,
            "completion_percent": 100 if decision.approved else 90,
            "current_step": "已由建立者通過" if decision.approved else "依驗收意見改善後重送",
        }
        updated = self.store.set_mission_status(
            mission_id,
            status,
            progress=progress,
            review_feedback=decision.feedback,
            approved_at=utc_now() if decision.approved else None,
        )
        await self.events.publish(
            "MissionApproved" if decision.approved else "MissionRejected",
            mission_id,
            {"feedback": decision.feedback},
            correlation_id=mission_id,
        )
        if decision.approved and mission.schedule == "monthly":
            await self.versions.approve_monthly_release(mission_id)
        return self.store.get_mission(mission_id) if not decision.approved else updated

    async def reopen(self, mission_id: str) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission.status is not MissionStatus.REJECTED:
            raise ValueError("Only a rejected mission can be reopened.")
        updated = self.store.set_mission_status(
            mission_id,
            MissionStatus.ACTIVE,
            progress={
                **mission.progress,
                "completion_percent": 0,
                "current_step": "依驗收意見重新規劃",
            },
        )
        await self.events.publish("MissionReopened", mission_id, {}, correlation_id=mission_id)
        return updated

    async def cancel(self, mission_id: str) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission.status is MissionStatus.APPROVED:
            raise ValueError("An approved mission remains in the permanent record.")
        updated = self.store.set_mission_status(
            mission_id,
            MissionStatus.CANCELLED,
            progress={"completion_percent": 0, "current_step": "已由建立者取消"},
        )
        await self.events.publish("MissionCancelled", mission_id, {}, correlation_id=mission_id)
        return updated

    def _import_legacy_challenges(self) -> None:
        existing_legacy_ids = {
            str(item.progress.get("legacy_challenge_id"))
            for item in self.store.list_missions(limit=10000)
            if item.progress.get("legacy_challenge_id")
        }
        for challenge in self.store.list_challenges(limit=10000):
            if challenge.challenge_id in existing_legacy_ids:
                continue
            mission = self.store.create_mission(
                MissionCreate(
                    title=challenge.title,
                    objective=challenge.objective,
                    completion_requirements=(
                        "完成固定成功條件並提交可追溯外部證據；最後必須由課題建立者勾選通過。"
                    ),
                    source="human",
                    schedule="manual",
                )
            )
            status = (
                MissionStatus.AWAITING_REVIEW
                if challenge.status
                in {ChallengeStatus.SUCCEEDED, ChallengeStatus.AWAITING_HUMAN}
                else MissionStatus.ACTIVE
            )
            self.store.set_mission_status(
                mission.mission_id,
                status,
                progress={
                    "completion_percent": 100 if status is MissionStatus.AWAITING_REVIEW else 0,
                    "current_step": challenge.next_action,
                    "legacy_challenge_id": challenge.challenge_id,
                },
                result_summary=(
                    "舊版課題契約已達成，等待建立者驗收。"
                    if status is MissionStatus.AWAITING_REVIEW
                    else ""
                ),
            )

    @staticmethod
    def _planning_task(mission: MissionRecord) -> TaskCreate:
        return TaskCreate(
            goal=f"為課題「{mission.title}」建立可稽核執行與能力計畫。",
            success_contract=SuccessContract(
                goal="Produce a bounded mission plan without claiming execution success.",
                checks=(
                    VerificationCheck(
                        name="Plan was produced",
                        path="metrics.completed",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                ),
                required_evidence=(EvidenceSource.TOOL,),
                require_reproducible=False,
                max_attempts=1,
                max_cost_units=50,
            ),
            action=ActionProposal(
                capability="task.plan",
                operation="plan",
                payload={
                    "objective": mission.objective,
                    "completion_requirements": mission.completion_requirements,
                },
                declared_risk=RiskLevel.LOW,
                reversible=True,
                estimated_cost_units=10,
            ),
            labels=(
                "lane:challenge",
                "no-learning",
                f"mission:{mission.mission_id}",
                f"priority:{mission.priority}",
            ),
        )
