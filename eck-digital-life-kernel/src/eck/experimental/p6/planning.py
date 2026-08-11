from __future__ import annotations

from eck.domain.enums import MissionStatus, MissionStepStatus
from eck.domain.models import EventRecord, MissionRecord, MissionStepDefinition, MissionStepRecord
from eck.experimental.p6.executor_base import MissionExecutorMixinBase


class MissionPlanningMixin(MissionExecutorMixinBase):
    async def handle_mission_created(self, event: EventRecord) -> None:
        if not self.settings.durable_mission_executor_enabled:
            return
        mission = self.store.get_mission(event.aggregate_id)
        if self._supports(mission):
            await self.compile(mission.mission_id)

    async def handle_plan_updated(self, event: EventRecord) -> None:
        if not self.settings.durable_mission_executor_enabled:
            return
        mission = self.store.get_mission(event.aggregate_id)
        if self._supports(mission):
            await self.compile(mission.mission_id)

    async def handle_mission_rejected(self, event: EventRecord) -> None:
        mission = self.store.get_mission(event.aggregate_id)
        if not self._supports(mission):
            return
        review_steps = [
            item
            for item in self.store.list_mission_steps(mission.mission_id)
            if item.action_kind == "quality.review"
        ]
        if not review_steps:
            self._append_quality_upgrade(mission)
            review_steps = [
                item
                for item in self.store.list_mission_steps(mission.mission_id)
                if item.action_kind == "quality.review"
            ]
        first_sequence = min(item.sequence for item in review_steps)
        reset = self.store.reset_mission_steps_from_sequence(
            mission.mission_id,
            sequence=first_sequence,
            reason="Creator feedback requires a new expert-review and improvement cycle.",
        )
        revision = int(mission.progress.get("human_revision_round", 0)) + 1
        self.store.set_mission_status(
            mission.mission_id,
            MissionStatus.ACTIVE,
            progress={
                **mission.progress,
                "executor": self._executor_version,
                "human_revision_round": revision,
                "completion_percent": 65,
                "current_step": "已接收驗收意見，重新執行三輪專家審查與改善",
                "reset_steps": reset,
            },
        )
        await self.events.publish(
            "MissionCreatorFeedbackQueued",
            mission.mission_id,
            {"revision": revision, "reset_steps": reset},
            correlation_id=mission.mission_id,
        )

    async def compile(self, mission_id: str) -> list[MissionStepRecord]:
        mission = self.store.get_mission(mission_id)
        existing = self.store.list_mission_steps(mission_id)
        if existing:
            return existing
        if not self._supports(mission):
            return []
        project_type = (
            "static_website"
            if self._website_request.search(f"{mission.title}\n{mission.objective}")
            else "python_project"
        )
        attempts = self.settings.mission_step_max_attempts
        definitions: list[MissionStepDefinition] = [
            MissionStepDefinition(
                step_key="workspace.prepare",
                sequence=10,
                action_kind="workspace.prepare",
                objective="建立與 ECK 核心隔離、受容量配額約束的任務工作區。",
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="reference.research",
                sequence=20,
                action_kind="reference.research",
                objective="檢索公開參考專案與已通過的相似 ECK 任務模式。",
                depends_on=("workspace.prepare",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="software.specify",
                sequence=30,
                action_kind="software.specify",
                objective="把使用者目標編譯為可驗證的軟體規格與驗收條件。",
                depends_on=("reference.research",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="architecture.design",
                sequence=40,
                action_kind="architecture.design",
                objective="由首席架構師建立資訊、視覺、互動、風險與品質契約。",
                depends_on=("software.specify",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="architecture.plan",
                sequence=50,
                action_kind="architecture.plan",
                objective="把架構拆成精確檔案、介面、檢查與可獨立審查的小任務。",
                depends_on=("architecture.design",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="software.implement",
                sequence=60,
                action_kind="software.implement",
                objective="依規格建立完整可執行來源檔，不以文字說明冒充成果。",
                depends_on=("architecture.plan",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
        ]
        microtasks = self._architect_microtask_definitions(
            project_type=project_type,
            previous="software.implement",
            sequence=70,
            attempts=attempts,
        )
        definitions.extend(microtasks)
        previous = microtasks[-1].step_key
        sequence = microtasks[-1].sequence + 10
        definitions.append(
            MissionStepDefinition(
                step_key="software.enhance",
                sequence=sequence,
                action_kind="software.enhance",
                objective="整合架構微任務，強化內容、視覺、互動、狀態與可及性。",
                depends_on=(previous,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            )
        )
        previous = "software.enhance"
        sequence += 10
        for round_number in range(1, self.settings.mission_internal_review_rounds + 1):
            review_key = f"quality.review.{round_number}"
            improve_key = f"quality.improve.{round_number}"
            definitions.extend(
                [
                    MissionStepDefinition(
                        step_key=review_key,
                        sequence=sequence,
                        action_kind="quality.review",
                        objective=(
                            f"獨立專家第 {round_number} 輪審查規格、內容、視覺、互動、"
                            "無障礙與維護性。"
                        ),
                        depends_on=(previous,),
                        inputs={"project_type": project_type, "round": round_number},
                        max_attempts=attempts,
                    ),
                    MissionStepDefinition(
                        step_key=improve_key,
                        sequence=sequence + 10,
                        action_kind="quality.improve",
                        objective=f"逐項修正第 {round_number} 輪專家發現並重建完整成果。",
                        depends_on=(review_key,),
                        inputs={
                            "project_type": project_type,
                            "round": round_number,
                            "review_step": review_key,
                        },
                        max_attempts=attempts,
                    ),
                ]
            )
            previous = improve_key
            sequence += 20
        definitions.extend(self._terminal_definitions(project_type, previous, sequence, attempts))
        steps = self.store.create_mission_steps(mission_id, tuple(definitions))
        progress = {
            **mission.progress,
            "execution_kind": "software_project",
            "executor": self._executor_version,
            "project_type": project_type,
            "step_count": len(steps),
            "completion_percent": 0,
            "current_step": "持久化任務圖已建立，等待執行第一個微任務",
        }
        self.store.set_mission_status(mission_id, MissionStatus.ACTIVE, progress=progress)
        await self.events.publish(
            "MissionExecutionCompiled",
            mission_id,
            {
                "executor": self._executor_version,
                "project_type": project_type,
                "step_count": len(steps),
            },
            correlation_id=mission_id,
        )
        return steps

    def upgrade_legacy_graphs(self) -> int:
        upgraded = 0
        for mission in self.store.list_missions(limit=1000):
            steps = self.store.list_mission_steps(mission.mission_id)
            if steps and mission.progress.get("executor") == "p6-durable-react.v2":
                failed_validation = next(
                    (
                        item
                        for item in steps
                        if item.action_kind == "software.validate"
                        and item.status is MissionStepStatus.FAILED
                        and self._mechanical_site_failure(item.last_error)
                    ),
                    None,
                )
                progress = {**mission.progress, "executor": self._executor_version}
                status = mission.status
                if failed_validation is not None and mission.status is MissionStatus.BLOCKED:
                    project_type = self._legacy_project_type(mission, steps)
                    reset_sequence = failed_validation.sequence
                    if project_type == "static_website":
                        self._write_project_files(
                            self._source_dir(mission.mission_id),
                            self._fallback_site_files(mission),
                        )
                        review_sequences = [
                            item.sequence
                            for item in steps
                            if item.action_kind == "quality.review"
                        ]
                        if review_sequences:
                            reset_sequence = min(review_sequences)
                    reset = self.store.reset_mission_steps_from_sequence(
                        mission.mission_id,
                        sequence=reset_sequence,
                        reason=(
                            "P6 v3 retries deterministic website contract repairs without "
                            "degradation."
                        ),
                    )
                    status = MissionStatus.ACTIVE
                    progress.update(
                        {
                            "failed_step": "",
                            "reset_steps": reset,
                            "current_step": "P6 v3 已修正驗證器修復流程，重新執行最終驗證",
                        }
                    )
                self.store.set_mission_status(
                    mission.mission_id,
                    status,
                    progress=progress,
                )
                upgraded += 1
                mission = self.store.get_mission(mission.mission_id)
                steps = self.store.list_mission_steps(mission.mission_id)
            failed_publish = next(
                (
                    item
                    for item in steps
                    if item.action_kind == "github.publish"
                    and item.status is MissionStepStatus.FAILED
                    and "repository not found" in item.last_error.casefold()
                ),
                None,
            )
            if failed_publish is not None and mission.status is MissionStatus.BLOCKED:
                reset = self.store.reset_mission_steps_from_sequence(
                    mission.mission_id,
                    sequence=failed_publish.sequence,
                    reason=(
                        "Retry GitHub publication with the dedicated account credential helper."
                    ),
                )
                self.store.set_mission_status(
                    mission.mission_id,
                    MissionStatus.ACTIVE,
                    progress={
                        **mission.progress,
                        "executor": self._executor_version,
                        "failed_step": None,
                        "reset_steps": reset,
                        "current_step": "GitHub 專用帳號憑證已隔離，重新推送已驗證成果。",
                    },
                )
                upgraded += 1
                mission = self.store.get_mission(mission.mission_id)
            if mission.status not in {
                MissionStatus.ACTIVE,
                MissionStatus.AWAITING_REVIEW,
                MissionStatus.REJECTED,
            }:
                continue
            steps = self.store.list_mission_steps(mission.mission_id)
            action_kinds = {item.action_kind for item in steps}
            legacy_graph = {
                "software.implement",
                "software.validate",
                "mission.submit",
            }.issubset(action_kinds)
            if (
                not steps
                or not legacy_graph
                or any(item.action_kind == "quality.review" for item in steps)
            ):
                continue
            upgraded_steps = self._append_quality_upgrade(mission)
            human_revision = int(mission.progress.get("human_revision_round", 0))
            if mission.review_feedback:
                human_revision = max(human_revision, 1)
            self.store.set_mission_status(
                mission.mission_id,
                MissionStatus.ACTIVE,
                progress={
                    **mission.progress,
                    "execution_kind": "software_project",
                    "executor": self._executor_version,
                    "project_type": self._legacy_project_type(mission, steps),
                    "step_count": len(upgraded_steps),
                    "human_revision_round": human_revision,
                    "completion_percent": 55,
                    "current_step": "舊版成果已轉入架構微任務與三輪專家改善流程",
                },
            )
            upgraded += 1
        return upgraded

    def _append_quality_upgrade(self, mission: MissionRecord) -> list[MissionStepRecord]:
        steps = self.store.list_mission_steps(mission.mission_id)
        if any(item.action_kind == "quality.review" for item in steps):
            return steps
        previous = max(steps, key=lambda item: item.sequence).step_key
        sequence = max(item.sequence for item in steps) + 10
        project_type = self._legacy_project_type(mission, steps)
        attempts = self.settings.mission_step_max_attempts
        definitions: list[MissionStepDefinition] = [
            MissionStepDefinition(
                step_key="reference.research.v2",
                sequence=sequence,
                action_kind="reference.research",
                objective="為舊任務補做可追溯參考研究與已核准模式檢索。",
                depends_on=(previous,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="architecture.design.v2",
                sequence=sequence + 10,
                action_kind="architecture.design",
                objective="為舊成果建立產品、視覺、互動與驗收架構契約。",
                depends_on=("reference.research.v2",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="architecture.plan.v2",
                sequence=sequence + 20,
                action_kind="architecture.plan",
                objective="把舊成果的改善契約拆成可獨立執行與驗證的微任務。",
                depends_on=("architecture.design.v2",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
        ]
        previous = "architecture.plan.v2"
        sequence += 30
        microtasks = self._architect_microtask_definitions(
            project_type=project_type,
            previous=previous,
            sequence=sequence,
            attempts=attempts,
            suffix=".v2",
        )
        definitions.extend(microtasks)
        previous = microtasks[-1].step_key
        sequence = microtasks[-1].sequence + 10
        definitions.append(
            MissionStepDefinition(
                step_key="software.enhance.v2",
                sequence=sequence,
                action_kind="software.enhance",
                objective="整合新版架構微任務後再進入獨立專家審查。",
                depends_on=(previous,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            )
        )
        previous = "software.enhance.v2"
        sequence += 10
        for round_number in range(1, self.settings.mission_internal_review_rounds + 1):
            review_key = f"quality.review.{round_number}"
            improve_key = f"quality.improve.{round_number}"
            definitions.extend(
                [
                    MissionStepDefinition(
                        step_key=review_key,
                        sequence=sequence,
                        action_kind="quality.review",
                        objective=f"獨立專家第 {round_number} 輪重新審查既有成果。",
                        depends_on=(previous,),
                        inputs={"project_type": project_type, "round": round_number},
                        max_attempts=attempts,
                    ),
                    MissionStepDefinition(
                        step_key=improve_key,
                        sequence=sequence + 10,
                        action_kind="quality.improve",
                        objective=f"逐項修正第 {round_number} 輪專家發現。",
                        depends_on=(review_key,),
                        inputs={
                            "project_type": project_type,
                            "round": round_number,
                            "review_step": review_key,
                        },
                        max_attempts=attempts,
                    ),
                ]
            )
            previous = improve_key
            sequence += 20
        definitions.extend(
            self._terminal_definitions(
                project_type,
                previous,
                sequence,
                attempts,
                suffix=".v2",
            )
        )
        return self.store.append_mission_steps(mission.mission_id, tuple(definitions))

    def _legacy_project_type(
        self,
        mission: MissionRecord,
        steps: list[MissionStepRecord],
    ) -> str:
        for step in steps:
            project_type = str(step.inputs.get("project_type", ""))
            if project_type in {"static_website", "python_project"}:
                return project_type
        return (
            "static_website"
            if self._website_request.search(f"{mission.title}\n{mission.objective}")
            else "python_project"
        )

    @staticmethod
    def _architect_microtask_definitions(
        *,
        project_type: str,
        previous: str,
        sequence: int,
        attempts: int,
        suffix: str = "",
    ) -> list[MissionStepDefinition]:
        definitions: list[MissionStepDefinition] = []
        dependency = previous
        for task_number in range(1, 7):
            step_key = f"software.microtask.{task_number}{suffix}"
            definitions.append(
                MissionStepDefinition(
                    step_key=step_key,
                    sequence=sequence,
                    action_kind="software.microtask",
                    objective=(
                        f"執行架構師計畫第 {task_number} 個微任務，"
                        "並以來源雜湊驗證實質改動。"
                    ),
                    depends_on=(dependency,),
                    inputs={
                        "project_type": project_type,
                        "task_index": task_number - 1,
                    },
                    max_attempts=attempts,
                )
            )
            dependency = step_key
            sequence += 10
        return definitions

    @staticmethod
    def _terminal_definitions(
        project_type: str,
        previous: str,
        sequence: int,
        attempts: int,
        *,
        suffix: str = "",
    ) -> list[MissionStepDefinition]:
        validate_key = f"software.validate{suffix}"
        learn_key = f"learning.distill{suffix}"
        package_key = f"artifact.package{suffix}"
        github_key = f"github.publish{suffix}"
        submit_key = f"mission.submit{suffix}"
        return [
            MissionStepDefinition(
                step_key=validate_key,
                sequence=sequence,
                action_kind="software.validate",
                objective="三輪專家改善後執行強化靜態契約或無網路 Docker 測試。",
                depends_on=(previous,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key=learn_key,
                sequence=sequence + 10,
                action_kind="learning.distill",
                objective="蒸餾架構、缺陷與修正方式，等待人工通過後供相似任務重用。",
                depends_on=(validate_key,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key=package_key,
                sequence=sequence + 20,
                action_kind="artifact.package",
                objective="封裝通過驗證的來源並產生 SHA-256 可追溯證據。",
                depends_on=(learn_key,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key=github_key,
                sequence=sequence + 30,
                action_kind="github.publish",
                objective="以主題加任務序號命名並推送至 ECK 專用 GitHub。",
                depends_on=(package_key,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key=submit_key,
                sequence=sequence + 40,
                action_kind="mission.submit",
                objective="彙整三輪審查與客觀證據後交由建立者最終驗收。",
                depends_on=(github_key,),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
        ]

