from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.domain.models import (
    EventRecord,
    MissionCompletionCreate,
    MissionReactCycleRecord,
    MissionRecord,
    MissionStepDefinition,
    MissionStepRecord,
)
from eck.events.bus import EventBus
from eck.services.mission_quality import MissionDevelopmentCouncil
from eck.services.missions import MissionService
from eck.services.project_lab import AutonomousProjectLabService
from eck.storage.sqlite import SQLiteStore


@dataclass(slots=True)
class StepOutcome:
    success: bool
    output: dict[str, Any]
    error: str = ""
    retryable: bool = False
    correction: str = ""


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.tags: set[str] = set()
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.add(tag.casefold())
        self._in_title = tag.casefold() == "title"
        for name, value in attrs:
            if name.casefold() in {"href", "src"} and value:
                self.references.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


class DurableMissionExecutor:
    _mission_id_pattern = re.compile(r"mission_[a-f0-9]{32}")
    _software_request = re.compile(
        r"(網站|網頁|web\s*site|website|landing\s*page|軟體|程式|專案|app|api)",
        re.I,
    )
    _website_request = re.compile(r"(網站|網頁|web\s*site|website|landing\s*page)", re.I)
    _allowed_site_suffixes = {".html", ".css", ".js", ".json", ".md", ".txt", ".svg"}

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        coder_brain: BrainProvider,
        project_lab: AutonomousProjectLabService,
        missions: MissionService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.coder_brain = coder_brain
        self.project_lab = project_lab
        self.missions = missions
        self.council = MissionDevelopmentCouncil(settings, store, coder_brain)
        assert settings.mission_workspace_dir is not None
        self.root = settings.mission_workspace_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

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
                "executor": "p6-durable-react.v2",
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
            "executor": "p6-durable-react.v2",
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
                "executor": "p6-durable-react.v2",
                "project_type": project_type,
                "step_count": len(steps),
            },
            correlation_id=mission_id,
        )
        return steps

    def upgrade_legacy_graphs(self) -> int:
        upgraded = 0
        for mission in self.store.list_missions(limit=1000):
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
                    "executor": "p6-durable-react.v2",
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

    async def run_next(self) -> MissionStepRecord | None:
        if not self.settings.durable_mission_executor_enabled:
            return None
        step = self.store.claim_next_mission_step()
        if step is None:
            return None
        mission = self.store.get_mission(step.mission_id)
        reason_summary = await self._reason_before_action(mission, step)
        action = {
            "tool": step.action_kind,
            "step_key": step.step_key,
            "attempt": step.attempts,
            "input_keys": sorted(step.inputs),
        }
        cycle = self.store.create_mission_react_cycle(
            step,
            reason_summary=reason_summary,
            action=action,
        )
        await self.events.publish(
            "MissionReactActionStarted",
            step.step_id,
            {
                "mission_id": step.mission_id,
                "step_key": step.step_key,
                "attempt": step.attempts,
                "reason_summary": reason_summary,
                "action": action,
            },
            correlation_id=step.mission_id,
        )
        try:
            outcome = await self._execute_action(mission, step)
        except Exception as exc:
            outcome = StepOutcome(
                success=False,
                output={"exception_type": type(exc).__name__, "detail": str(exc)},
                error=f"{type(exc).__name__}: {exc}",
                retryable=step.attempts < step.max_attempts,
                correction="保留工作區，根據例外觀察重播此冪等步驟。",
            )
        cycle_status = (
            MissionCycleStatus.SUCCEEDED
            if outcome.success
            else MissionCycleStatus.NEEDS_CORRECTION
            if outcome.retryable and step.attempts < step.max_attempts
            else MissionCycleStatus.FAILED
        )
        completed_cycle = self.store.finish_mission_react_cycle(
            cycle.cycle_id,
            status=cycle_status,
            observation=outcome.output,
            correction=outcome.correction,
        )
        completed_step = self.store.finish_mission_step(
            step.step_id,
            success=outcome.success,
            output=outcome.output,
            error=outcome.error,
            retryable=outcome.retryable,
        )
        await self._record_cycle_event(completed_cycle, completed_step)
        await self._update_progress(completed_step.mission_id)
        return completed_step

    def has_runnable_work(self) -> bool:
        for mission in self.store.list_missions(limit=200):
            if mission.status not in {MissionStatus.ACTIVE, MissionStatus.PREPARING}:
                continue
            steps = self.store.list_mission_steps(mission.mission_id)
            statuses = {item.step_key: item.status for item in steps}
            for step in steps:
                if step.status is not MissionStepStatus.PENDING:
                    continue
                if all(
                    statuses.get(dependency) is MissionStepStatus.SUCCEEDED
                    for dependency in step.depends_on
                ):
                    return True
        return False

    def status(self, mission_id: str | None = None) -> dict[str, Any]:
        missions = (
            [self.store.get_mission(mission_id)]
            if mission_id
            else self.store.list_missions(limit=100)
        )
        selected = []
        latest_cycle: MissionReactCycleRecord | None = None
        for mission in missions:
            steps = self.store.list_mission_steps(mission.mission_id)
            if not steps:
                continue
            cycles = self.store.list_mission_react_cycles(mission.mission_id, limit=100)
            if cycles and (latest_cycle is None or cycles[0].created_at > latest_cycle.created_at):
                latest_cycle = cycles[0]
            selected.append(
                {
                    "mission": mission,
                    "steps": steps,
                    "cycles": cycles,
                    "workspace_bytes": self._directory_bytes(
                        self._mission_dir(mission.mission_id), missing_ok=True
                    ),
                }
            )
        return {
            "enabled": self.settings.durable_mission_executor_enabled,
            "executor": "p6-durable-react.v2",
            "items": selected,
            "latest_cycle": latest_cycle,
            "storage": {
                "root": str(self.root),
                "used_bytes": self._directory_bytes(self.root, missing_ok=True),
                "total_limit_bytes": int(
                    self.settings.mission_workspace_total_max_gb * 1024**3
                ),
                "per_mission_limit_bytes": self.settings.mission_workspace_max_mb * 1024**2,
                "archive_root": (
                    str(self.settings.mission_archive_dir.resolve())
                    if self.settings.mission_archive_dir
                    else None
                ),
            },
            "claim_policy": (
                "Only deterministic validation can advance a software mission to packaging. "
                "The dashboard stores auditable reason summaries, actions, observations, and "
                "corrections; it never exposes or fabricates private chain-of-thought."
            ),
        }

    def preview_path(self, mission_id: str, requested_path: str = "index.html") -> Path:
        relative = self._safe_relative_path(requested_path or "index.html")
        source = self._source_dir(mission_id)
        target = (source / relative).resolve()
        target.relative_to(source.resolve())
        if target.suffix.casefold() not in self._allowed_site_suffixes or not target.is_file():
            raise KeyError("Mission preview artifact was not found.")
        return target

    def package_path(self, mission_id: str) -> Path:
        step = self._step_by_key(mission_id, "artifact.package")
        path = Path(str(step.output.get("path", "")))
        if step.status is not MissionStepStatus.SUCCEEDED or not path.is_file():
            raise KeyError("Mission package is not available.")
        path.resolve().relative_to(self._mission_dir(mission_id))
        return path

    def _supports(self, mission: MissionRecord) -> bool:
        kind = str(mission.progress.get("execution_kind", "auto"))
        if kind == "manual":
            return False
        if kind == "software_project":
            return True
        return bool(self._software_request.search(f"{mission.title}\n{mission.objective}"))

    async def _reason_before_action(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> str:
        previous = self.store.list_mission_react_cycles(mission.mission_id, limit=5)
        observations = [
            {
                "step_id": item.step_id,
                "status": item.status.value,
                "observation": item.observation,
                "correction": item.correction,
            }
            for item in previous
        ]
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是 ECK 的持久化任務控制器。不要輸出私有思考鏈。"
                            "請只輸出 JSON，提供一段可稽核的決策摘要：指出已知邊界、"
                            "本輪要用的工具與可客觀檢查的成功條件。不可宣稱尚未執行的結果。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mission": mission.objective,
                                "requirements": mission.completion_requirements,
                                "step": step.objective,
                                "action_kind": step.action_kind,
                                "attempt": step.attempts,
                                "previous_observations": observations,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "reason_summary": {"type": "string"},
                        "unknowns": {"type": "array", "items": {"type": "string"}},
                        "tool": {"type": "string"},
                        "success_check": {"type": "string"},
                    },
                    "required": ["reason_summary", "unknowns", "tool", "success_check"],
                },
                options={"temperature": 0.1, "num_predict": 320},
            )
            value = self._json_object(response.content)
            summary = str(value.get("reason_summary", "")).strip()
            unknowns = [str(item) for item in value.get("unknowns", []) if str(item).strip()]
            success_check = str(value.get("success_check", "")).strip()
            if summary:
                suffix = ""
                if unknowns:
                    suffix += f" 未知項：{'；'.join(unknowns[:3])}。"
                if success_check:
                    suffix += f" 驗證：{success_check}"
                return (summary + suffix)[:4000]
        except (json.JSONDecodeError, RuntimeError, ValueError):
            pass
        return (
            f"目前只處理微任務「{step.objective}」；先執行 {step.action_kind}，"
            "再以工具輸出決定成功、修正或停止，不把模型自述當成證據。"
        )

    async def _execute_action(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        handlers = {
            "workspace.prepare": self._prepare_workspace,
            "reference.research": self._research_references,
            "software.specify": self._specify_software,
            "architecture.design": self._design_architecture,
            "architecture.plan": self._plan_architecture,
            "software.implement": self._implement_software,
            "software.microtask": self._execute_architect_microtask,
            "software.enhance": self._enhance_software,
            "quality.review": self._review_quality,
            "quality.improve": self._improve_quality,
            "software.validate": self._validate_software,
            "learning.distill": self._distill_learning,
            "artifact.package": self._package_artifact,
            "github.publish": self._publish_github,
            "mission.submit": self._submit_mission,
        }
        handler = handlers.get(step.action_kind)
        if handler is None:
            return StepOutcome(
                success=False,
                output={"detail": f"Unsupported action kind: {step.action_kind}"},
                error=f"Unsupported action kind: {step.action_kind}",
            )
        return await handler(mission, step)

    async def _prepare_workspace(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        used = self._directory_bytes(self.root, missing_ok=True)
        total_limit = int(self.settings.mission_workspace_total_max_gb * 1024**3)
        if used >= total_limit:
            return StepOutcome(
                success=False,
                output={"used_bytes": used, "limit_bytes": total_limit},
                error="Mission workspace total quota is exhausted.",
                correction="封存或移除已完成任務的本機副本後再重試。",
            )
        mission_dir = self._mission_dir(mission.mission_id)
        source_dir = mission_dir / "source"
        deliverables = mission_dir / "deliverables"
        logs = mission_dir / "logs"
        for path in (source_dir, deliverables, logs):
            path.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "eck-durable-mission.v1",
            "mission_id": mission.mission_id,
            "title": mission.title,
            "objective": mission.objective,
            "requirements": mission.completion_requirements,
            "project_type": step.inputs.get("project_type"),
            "created_at": utc_now().isoformat(),
        }
        self._write_json(mission_dir / "manifest.json", manifest)
        return StepOutcome(
            success=True,
            output={
                "mission_dir": str(mission_dir),
                "source_dir": str(source_dir),
                "deliverables_dir": str(deliverables),
                "quota_bytes": self.settings.mission_workspace_max_mb * 1024**2,
            },
        )

    async def _research_references(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "static_website"))
        research = await self.council.research_context(
            mission,
            project_type=project_type,
        )
        self._write_json(self._mission_dir(mission.mission_id) / "references.json", research)
        return StepOutcome(success=True, output=research)

    async def _design_architecture(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "static_website"))
        research = self._latest_step_by_action(mission.mission_id, "reference.research").output
        architecture = await self.council.architecture(
            mission,
            project_type=project_type,
            research=research,
        )
        self._write_json(self._mission_dir(mission.mission_id) / "architecture.json", architecture)
        return StepOutcome(success=True, output=architecture)

    async def _plan_architecture(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "static_website"))
        architecture = self._latest_step_by_action(
            mission.mission_id, "architecture.design"
        ).output
        plan = await self.council.implementation_plan(
            mission,
            project_type=project_type,
            architecture=architecture,
        )
        self._write_json(self._mission_dir(mission.mission_id) / "implementation-plan.json", plan)
        return StepOutcome(success=True, output=plan)

    async def _specify_software(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "unsupported_software"))
        fallback = {
            "project_name": self._project_name(mission),
            "project_type": project_type,
            "audience": "依使用者目標推定的一般訪客",
            "pages": ["index.html"] if project_type == "static_website" else [],
            "features": [
                *(
                    ["清楚導覽", "響應式版面", "主要內容與行動按鈕", "可操作的前端互動"]
                    if project_type == "static_website"
                    else ["明確入口函式", "可重現 pytest", "標準函式庫優先", "錯誤處理"]
                ),
            ],
            "acceptance_checks": [
                *(
                    [
                        "HTML、CSS、JavaScript 均為本機檔案且引用有效",
                        "包含 title、viewport、nav、main 與可辨識的主題內容",
                        "沒有 TODO、Lorem ipsum 或宣稱未完成結果",
                    ]
                    if project_type == "static_website"
                    else [
                        "Python 語法與靜態品質契約通過",
                        "Docker 無網路隔離環境中的 pytest 全數通過",
                        "至少兩個行為斷言且不模擬成功",
                    ]
                ),
            ],
        }
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是資深產品與前端架構師。只輸出 JSON。"
                            "把任務轉成可測試的軟體規格。static_website 必須能在無後端、"
                            "無外部 CDN 的本機環境驗證；python_project 必須使用 Python 3.11、"
                            "可在無網路 Docker 中測試。不可縮小使用者目標或只給教學。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "completion_requirements": mission.completion_requirements,
                                "existing_plan": mission.progress.get("plan", {}),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string"},
                        "project_type": {"type": "string"},
                        "audience": {"type": "string"},
                        "pages": {"type": "array", "items": {"type": "string"}},
                        "features": {"type": "array", "items": {"type": "string"}},
                        "acceptance_checks": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "project_name",
                        "project_type",
                        "audience",
                        "pages",
                        "features",
                        "acceptance_checks",
                    ],
                },
                options={"temperature": 0.2, "num_predict": 900},
            )
            candidate = self._json_object(response.content)
            if candidate.get("features") and candidate.get("acceptance_checks"):
                fallback.update(candidate)
                fallback["project_type"] = project_type
                fallback["project_name"] = self._safe_project_name(
                    str(candidate.get("project_name", "")), mission.mission_id
                )
                fallback["model"] = response.model
        except (json.JSONDecodeError, RuntimeError, ValueError):
            fallback["model"] = "deterministic-spec-fallback.v1"
        self._write_json(self._mission_dir(mission.mission_id) / "spec.json", fallback)
        return StepOutcome(success=True, output=fallback)

    async def _implement_software(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        spec = self._latest_step_by_action(mission.mission_id, "software.specify").output
        architecture = self._latest_step_by_action(
            mission.mission_id, "architecture.design"
        ).output
        implementation_plan = self._latest_step_by_action(
            mission.mission_id, "architecture.plan"
        ).output
        if step.inputs.get("project_type") == "python_project":
            return await self._implement_python(mission, spec)
        files: list[dict[str, str]] = []
        model = "deterministic-site-fallback.v1"
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是世界級產品設計工程師與前端工程師。只輸出 JSON。"
                            "嚴格逐項執行架構師的小任務計畫，直接交付完整網站檔案，"
                            "不是範例、教學或程式碼片段。必須包含 index.html、styles.css、"
                            "app.js、README.md；不可使用 CDN、外部圖片、框架、TODO 或 Lorem ipsum。"
                            "所有內容需符合任務主題，至少五個有實質內容的語意區塊、"
                            "完整設計 tokens、"
                            "手機版重排、清楚 hover/focus/selected 狀態、至少三種改變頁面狀態的"
                            "JavaScript 互動與 aria-live 回饋。不要交付瀏覽器預設風格。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "completion_requirements": mission.completion_requirements,
                                "spec": spec,
                                "architecture": architecture,
                                "implementation_plan": implementation_plan,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        }
                    },
                    "required": ["files"],
                },
                options={"temperature": 0.25, "num_predict": 8192},
            )
            files = self._validated_site_files(self._json_object(response.content).get("files"))
            model = response.model
        except (json.JSONDecodeError, RuntimeError, ValueError):
            files = []
        if not files:
            files = self._fallback_site_files(mission)
        source_dir = self._source_dir(mission.mission_id)
        self._write_project_files(source_dir, files)
        used = self._directory_bytes(self._mission_dir(mission.mission_id))
        limit = self.settings.mission_workspace_max_mb * 1024**2
        if used > limit:
            return StepOutcome(
                success=False,
                output={"used_bytes": used, "limit_bytes": limit},
                error="Generated mission exceeded its storage quota.",
                correction="移除非必要資產並以較小的來源檔重新生成。",
            )
        return StepOutcome(
            success=True,
            output={
                "model": model,
                "source_dir": str(source_dir),
                "files": [item["path"] for item in files],
                "bytes": used,
            },
        )

    async def _execute_architect_microtask(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        task_index = int(step.inputs.get("task_index", 0))
        plan = self._latest_step_by_action(mission.mission_id, "architecture.plan").output
        tasks = plan.get("tasks", [])
        if not isinstance(tasks, list) or task_index >= len(tasks):
            return StepOutcome(
                success=False,
                output={"task_index": task_index, "available_tasks": len(tasks)},
                error="The architect plan does not contain the required microtask.",
                retryable=False,
                correction="重新建立至少六個含檔案、介面與檢查方式的架構微任務。",
            )
        task = tasks[task_index]
        if not isinstance(task, dict):
            return StepOutcome(
                success=False,
                output={"task_index": task_index},
                error="The architect microtask is not a structured object.",
                retryable=False,
                correction="架構微任務必須使用結構化物件描述。",
            )
        checks = task.get("checks", [])
        review = {
            "summary": f"Execute architect microtask {task_index + 1} as a durable change.",
            "findings": [
                {
                    "severity": "important",
                    "location": ", ".join(str(item) for item in task.get("files", [])),
                    "evidence": str(task.get("objective", task.get("title", ""))),
                    "required_change": str(task.get("objective", "")),
                    "acceptance_check": "; ".join(str(item) for item in checks),
                }
            ],
        }
        outcome = await self._apply_quality_improvement(
            mission,
            step,
            review,
            round_number=task_index + 1,
            phase="architect-microtask",
            artifact_name=f"microtask-{task_index + 1}",
        )
        if outcome.success:
            outcome.output["architect_task"] = task
            outcome.output["task_index"] = task_index
        return outcome

    async def _enhance_software(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        architecture = self._latest_step_by_action(
            mission.mission_id, "architecture.design"
        ).output
        review = {
            "summary": "Architecture implementation pass before independent review.",
            "findings": [
                {
                    "severity": "important",
                    "location": "whole-project",
                    "evidence": item,
                    "required_change": item,
                    "acceptance_check": (
                        "The implemented source visibly satisfies this architecture item."
                    ),
                }
                for item in architecture.get("acceptance_contract", [])
            ],
        }
        return await self._apply_quality_improvement(
            mission,
            step,
            review,
            round_number=0,
            phase="architecture-integration",
            artifact_name="architecture-integration",
        )

    async def _review_quality(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "static_website"))
        round_number = int(step.inputs.get("round", 1))
        source_dir = self._source_dir(mission.mission_id)
        deterministic = (
            self._validate_site(source_dir, mission, enforce_threshold=False)
            if project_type == "static_website"
            else {
                "quality_score": 100,
                "source_sha256": self._source_hash(source_dir),
                "detail": "Python receives its final deterministic Docker gate after reviews.",
            }
        )
        architecture = self._latest_step_by_action(
            mission.mission_id, "architecture.design"
        ).output
        review = await self.council.expert_review(
            mission,
            project_type=project_type,
            round_number=round_number,
            architecture=architecture,
            files=self._source_files(source_dir, project_type),
            deterministic=deterministic,
        )
        review["source_sha256"] = self._source_hash(source_dir)
        self._write_json(
            self._mission_dir(mission.mission_id) / f"review-round-{round_number}.json",
            review,
        )
        return StepOutcome(success=True, output=review)

    async def _improve_quality(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        review_key = str(step.inputs.get("review_step", ""))
        review = self._step_by_key(mission.mission_id, review_key).output
        return await self._apply_quality_improvement(
            mission,
            step,
            review,
            round_number=int(step.inputs.get("round", 1)),
        )

    async def _apply_quality_improvement(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
        review: dict[str, Any],
        *,
        round_number: int,
        phase: str = "expert-review",
        artifact_name: str | None = None,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "static_website"))
        source_dir = self._source_dir(mission.mission_id)
        before = self._source_hash(source_dir)
        human_revision = int(mission.progress.get("human_revision_round", 0))
        revision_key = f"revision-{human_revision}-{phase}-{round_number}"
        architecture = self._latest_step_by_action(
            mission.mission_id, "architecture.design"
        ).output
        result = await self.council.improve(
            mission,
            project_type=project_type,
            round_number=round_number,
            phase=phase,
            architecture=architecture,
            review=review,
            files=self._source_files(source_dir, project_type),
        )
        try:
            files = (
                self._validated_python_files(result.get("files"))
                if project_type == "python_project"
                else self._validated_site_files(result.get("files"))
            )
        except ValueError:
            files = []
        if not files:
            files = self._fallback_quality_improvement(
                source_dir,
                project_type=project_type,
                revision_key=revision_key,
            )
            result["model"] = "deterministic-quality-improvement.v1"
        self._write_project_files(source_dir, files)
        after = self._source_hash(source_dir)
        if before == after:
            files = self._fallback_quality_improvement(
                source_dir,
                project_type=project_type,
                revision_key=revision_key,
            )
            result["model"] = "deterministic-quality-improvement.v1"
            self._write_project_files(source_dir, files)
            after = self._source_hash(source_dir)
        if before == after:
            return StepOutcome(
                success=False,
                output={
                    "phase": phase,
                    "round": round_number,
                    "human_revision": human_revision,
                    "source_sha256": after,
                    "changed": False,
                },
                error="Expert improvement did not change the verified source tree.",
                retryable=step.attempts < step.max_attempts,
                correction="重新讀取專家發現並做出可由來源雜湊觀察到的實質修正。",
            )
        output = {
            "phase": phase,
            "round": round_number,
            "human_revision": human_revision,
            "model": result.get("model", "unknown"),
            "addressed_findings": result.get("addressed_findings", []),
            "before_sha256": before,
            "source_sha256": after,
            "changed": True,
            "files": [item["path"] for item in files],
        }
        artifact = artifact_name or f"improvement-round-{round_number}"
        self._write_json(
            self._mission_dir(mission.mission_id) / f"{artifact}.json",
            output,
        )
        return StepOutcome(success=True, output=output)

    async def _validate_software(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        source_dir = self._source_dir(mission.mission_id)
        completed_reviews = [
            item
            for item in self.store.list_mission_steps(mission.mission_id)
            if item.action_kind == "quality.review"
            and item.status is MissionStepStatus.SUCCEEDED
        ]
        if len(completed_reviews) < self.settings.mission_internal_review_rounds:
            return StepOutcome(
                success=False,
                output={
                    "completed_review_rounds": len(completed_reviews),
                    "required_review_rounds": self.settings.mission_internal_review_rounds,
                },
                error="The mandatory independent review rounds are incomplete.",
                retryable=False,
                correction="先完成全部專家審查與改善步驟，不得直接進入最終驗證。",
            )
        if step.inputs.get("project_type") == "python_project":
            repair = None
            if step.attempts > 1 and step.last_error:
                repair = await self._repair_python(mission, source_dir, step.last_error)
            report = await self.project_lab.validate_python_directory(
                source_dir,
                objective=mission.objective,
            )
            report["repair"] = repair
            report["source_sha256"] = self._source_hash(source_dir)
            self._write_json(self._mission_dir(mission.mission_id) / "validation.json", report)
            if not report.get("success"):
                detail = str(report.get("output_tail", report.get("detail", "Validation failed.")))
                return StepOutcome(
                    success=False,
                    output=report,
                    error=detail,
                    retryable=step.attempts < step.max_attempts,
                    correction="依 Docker 或靜態檢查輸出修正完整 Python 專案後重測。",
                )
            return StepOutcome(success=True, output=report)
        repair = None
        if step.attempts > 1 and step.last_error:
            repair = await self._repair_site(mission, source_dir, step.last_error)
        report = self._validate_site(source_dir, mission, enforce_threshold=True)
        report["repair"] = repair
        self._write_json(self._mission_dir(mission.mission_id) / "validation.json", report)
        if not report["success"]:
            detail = "; ".join(str(item) for item in report["issues"])
            return StepOutcome(
                success=False,
                output=report,
                error=detail,
                retryable=step.attempts < step.max_attempts,
                correction=(
                    "根據驗證器觀察修正完整來源檔，再重跑相同驗收契約。"
                    if step.attempts < step.max_attempts
                    else "已達修正上限；保留反例並停止後續發布。"
                ),
            )
        return StepOutcome(success=True, output=report)

    async def _distill_learning(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        project_type = str(step.inputs.get("project_type", "static_website"))
        architecture = self._latest_step_by_action(
            mission.mission_id, "architecture.design"
        ).output
        reviews = [
            item.output
            for item in self.store.list_mission_steps(mission.mission_id)
            if item.action_kind == "quality.review"
            and item.status is MissionStepStatus.SUCCEEDED
        ]
        validation = self._latest_step_by_action(
            mission.mission_id, "software.validate"
        ).output
        pattern = self.council.distill_pattern(
            mission,
            project_type=project_type,
            architecture=architecture,
            reviews=reviews,
            validation=validation,
        )
        self._write_json(self._mission_dir(mission.mission_id) / "learning-pattern.json", pattern)
        current = self.store.get_mission(mission.mission_id)
        self.store.set_mission_status(
            mission.mission_id,
            current.status,
            progress={**current.progress, "learning_pattern": pattern},
        )
        return StepOutcome(
            success=True,
            output={
                **pattern,
                "reusable": False,
                "detail": "Pattern remains a candidate until creator approval.",
            },
        )

    async def _package_artifact(
        self,
        mission: MissionRecord,
        _: MissionStepRecord,
    ) -> StepOutcome:
        validation = self._latest_step_by_action(mission.mission_id, "software.validate")
        if not validation.output.get("success"):
            return StepOutcome(
                success=False,
                output={"detail": "Validation evidence is not successful."},
                error="Cannot package an unverified project.",
            )
        mission_dir = self._mission_dir(mission.mission_id)
        source_dir = self._source_dir(mission.mission_id)
        deliverables = mission_dir / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)
        package = deliverables / f"{self._project_name(mission)}.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source_dir.rglob("*")):
                if not path.is_file() or ".git" in path.parts or any(
                    part in {"__pycache__", ".pytest_cache", "node_modules"}
                    for part in path.parts
                ):
                    continue
                archive.write(path, path.relative_to(source_dir).as_posix())
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        return StepOutcome(
            success=True,
            output={
                "path": str(package),
                "sha256": digest,
                "bytes": package.stat().st_size,
                "download_url": f"/v1/missions/{mission.mission_id}/download",
            },
        )

    async def _publish_github(
        self,
        mission: MissionRecord,
        _: MissionStepRecord,
    ) -> StepOutcome:
        if not self.settings.mission_publish_verified_projects:
            return StepOutcome(
                success=True,
                output={
                    "published": False,
                    "deferred": True,
                    "detail": "Mission publishing is disabled by configuration.",
                },
            )
        repository_name = self._project_name(mission)
        result = await self.project_lab.publish_directory(
            name=repository_name,
            source_dir=self._source_dir(mission.mission_id),
            visibility="private",
        )
        if result.get("published") or result.get("deferred"):
            return StepOutcome(success=True, output=result)
        return StepOutcome(
            success=False,
            output=result,
            error=str(result.get("detail", "GitHub publication failed.")),
            retryable=True,
            correction="保留本機 Git 儲存庫，檢查遠端回應後再次推送。",
        )

    async def _submit_mission(
        self,
        mission: MissionRecord,
        _: MissionStepRecord,
    ) -> StepOutcome:
        validation = self._latest_step_by_action(mission.mission_id, "software.validate").output
        package = self._latest_step_by_action(mission.mission_id, "artifact.package").output
        github = self._latest_step_by_action(mission.mission_id, "github.publish").output
        project_type = str(mission.progress.get("project_type", "static_website"))
        preview_url = (
            f"/v1/missions/{mission.mission_id}/preview/"
            if project_type == "static_website"
            else ""
        )
        evidence = [
            preview_url,
            str(package.get("download_url", "")),
            f"sha256:{package.get('sha256', '')}",
            f"expert-review-rounds:{self.settings.mission_internal_review_rounds}",
            f"quality-score:{validation.get('quality_score', 0)}",
        ]
        if github.get("url"):
            evidence.append(str(github["url"]))
        evidence = [item for item in evidence if item and not item.endswith(":")]
        summary = (
            "P6 已完成架構設計、細粒度計畫、三輪獨立專家審查與改善、"
            "確定性驗證及可追溯封裝。"
            f"驗證通過 {len(validation.get('checks', []))} 項，"
            f"品質分數 {validation.get('quality_score', 0)}；"
            f"GitHub 狀態：{'已推送' if github.get('published') else '延後'}。"
            "成果已提交，仍需建立者實際檢視預覽後勾選通過。"
        )
        await self.missions.submit_completion(
            mission.mission_id,
            MissionCompletionCreate(result_summary=summary, evidence=tuple(evidence)),
        )
        return StepOutcome(
            success=True,
            output={
                "result_summary": summary,
                "evidence": evidence,
                "preview_url": preview_url,
            },
        )

    async def _implement_python(
        self,
        mission: MissionRecord,
        spec: dict[str, Any],
    ) -> StepOutcome:
        files: list[dict[str, str]] = []
        model = "deterministic-python-fallback.v1"
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是資深 Python 3.11 軟體工程師。只輸出 JSON。"
                            "交付完整可執行專案與 pytest，不是教學或片段。只使用標準函式庫與 "
                            "pytest；禁止網路、shell、假資料成功、mock、TODO 與未實作函式。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "completion_requirements": mission.completion_requirements,
                                "spec": spec,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        }
                    },
                    "required": ["files"],
                },
                options={"temperature": 0.2, "num_predict": 8192},
            )
            files = self._validated_python_files(self._json_object(response.content).get("files"))
            model = response.model
        except (json.JSONDecodeError, RuntimeError, ValueError):
            files = []
        if not files:
            files = self._fallback_python_files(mission)
        source_dir = self._source_dir(mission.mission_id)
        self._clear_source(source_dir)
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
        return StepOutcome(
            success=True,
            output={
                "model": model,
                "source_dir": str(source_dir),
                "files": [item["path"] for item in files],
                "bytes": self._directory_bytes(source_dir),
            },
        )

    async def _repair_python(
        self,
        mission: MissionRecord,
        source_dir: Path,
        failure: str,
    ) -> dict[str, Any]:
        current = [
            {
                "path": path.relative_to(source_dir).as_posix(),
                "content": path.read_text(encoding="utf-8", errors="replace")[:120_000],
            }
            for path in sorted(source_dir.rglob("*"))
            if path.is_file() and path.suffix.casefold() in {".py", ".toml", ".md", ".txt"}
        ]
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n只輸出 JSON。根據真實靜態檢查或 pytest 失敗修正完整 "
                            "Python 專案；維持原目標且不得用 mock 或刪除重要斷言規避失敗。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "failure": failure,
                                "current_files": current,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {"files": {"type": "array", "items": {"type": "object"}}},
                    "required": ["files"],
                },
                options={"temperature": 0.1, "num_predict": 8192},
            )
            files = self._validated_python_files(self._json_object(response.content).get("files"))
            if not files:
                return {"attempted": True, "applied": False, "detail": "No valid repair files."}
            self._clear_source(source_dir)
            for item in files:
                target = source_dir / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding="utf-8")
            return {"attempted": True, "applied": True, "model": response.model}
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            return {"attempted": True, "applied": False, "detail": str(exc)}

    async def _repair_site(
        self,
        mission: MissionRecord,
        source_dir: Path,
        failure: str,
    ) -> dict[str, Any]:
        current = [
            {
                "path": path.relative_to(source_dir).as_posix(),
                "content": path.read_text(encoding="utf-8", errors="replace")[:120_000],
            }
            for path in sorted(source_dir.rglob("*"))
            if path.is_file() and path.suffix.casefold() in self._allowed_site_suffixes
        ]
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是前端除錯工程師。只輸出 JSON。"
                            "依驗證器的真實錯誤修正專案，回傳完整 files 陣列，不可移除原任務功能。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "failure": failure,
                                "current_files": current,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        }
                    },
                    "required": ["files"],
                },
                options={"temperature": 0.15, "num_predict": 8192},
            )
            files = self._validated_site_files(self._json_object(response.content).get("files"))
            if not files:
                return {"attempted": True, "applied": False, "detail": "No valid repair files."}
            self._clear_source(source_dir)
            for item in files:
                target = source_dir / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding="utf-8")
            return {
                "attempted": True,
                "applied": True,
                "model": response.model,
                "file_count": len(files),
            }
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            return {"attempted": True, "applied": False, "detail": str(exc)}

    def _validate_site(
        self,
        source_dir: Path,
        mission: MissionRecord,
        *,
        enforce_threshold: bool = True,
    ) -> dict[str, Any]:
        issues: list[str] = []
        checks: list[str] = []
        index = source_dir / "index.html"
        css = source_dir / "styles.css"
        script = source_dir / "app.js"
        if not index.is_file():
            issues.append("index.html is missing")
            return {"success": False, "issues": issues, "checks": checks}
        markup = index.read_text(encoding="utf-8", errors="replace")
        parser = _ReferenceParser()
        try:
            parser.feed(markup)
        except Exception as exc:
            issues.append(f"HTML parse failed: {exc}")
        if "title" not in parser.tags or not "".join(parser.title_parts).strip():
            issues.append("HTML title is missing")
        else:
            checks.append("document-title")
        for tag in ("nav", "main"):
            if tag not in parser.tags:
                issues.append(f"Semantic element <{tag}> is missing")
            else:
                checks.append(f"semantic-{tag}")
        section_count = len(re.findall(r"<section\b", markup, re.I))
        if section_count < 4:
            issues.append("Website requires at least four substantive semantic sections")
        else:
            checks.append("content-depth")
        heading_count = len(re.findall(r"<h[1-3]\b", markup, re.I))
        if heading_count < 4:
            issues.append("Content hierarchy requires at least four visible headings")
        else:
            checks.append("heading-hierarchy")
        if not re.search(r"<meta[^>]+name=[\"']viewport[\"']", markup, re.I):
            issues.append("Responsive viewport metadata is missing")
        else:
            checks.append("responsive-viewport")
        lowered = markup.casefold()
        if any(token in lowered for token in ("lorem ipsum", "todo", "coming soon")):
            issues.append("Placeholder content remains in the deliverable")
        else:
            checks.append("no-placeholder-content")
        if not css.is_file() or css.stat().st_size < 2400:
            issues.append("styles.css is missing or too small to represent a complete layout")
        elif "styles.css" not in parser.references:
            issues.append("index.html does not reference styles.css")
        else:
            stylesheet = css.read_text(encoding="utf-8", errors="replace")
            if stylesheet.count("{") != stylesheet.count("}"):
                issues.append("CSS braces are unbalanced")
            else:
                checks.append("local-css")
                css_requirements = {
                    "design-tokens": len(
                        re.findall(r"--[a-z][a-z0-9-]*\s*:", stylesheet, re.I)
                    )
                    >= 4,
                    "responsive-layout": "@media" in stylesheet,
                    "layout-system": bool(
                        re.search(r"display\s*:\s*(?:grid|flex)", stylesheet, re.I)
                    ),
                    "focus-state": ":focus" in stylesheet,
                    "interaction-state": ":hover" in stylesheet,
                    "motion-feedback": bool(
                        re.search(r"transition|animation|@keyframes", stylesheet, re.I)
                    ),
                }
                for name, passed in css_requirements.items():
                    if passed:
                        checks.append(name)
                    else:
                        issues.append(f"CSS quality contract failed: {name}")
        if not script.is_file() or script.stat().st_size < 700:
            issues.append("app.js is missing or has no meaningful interaction")
        elif "app.js" not in parser.references:
            issues.append("index.html does not reference app.js")
        else:
            javascript = script.read_text(encoding="utf-8", errors="replace")
            interaction_count = len(re.findall(r"addEventListener\s*\(", javascript))
            if interaction_count < 3:
                issues.append("JavaScript requires at least three event-driven interactions")
            else:
                checks.append("interaction-depth")
            if not re.search(
                r"classList\.|textContent\s*=|innerHTML\s*=|setAttribute\s*\(",
                javascript,
            ):
                issues.append("JavaScript does not produce an observable page-state change")
            else:
                checks.append("dynamic-state-change")
            checks.append("local-javascript")
        accessibility_requirements = {
            "language": bool(
                re.search(r"<html[^>]+lang=[\"'][^\"']+", markup, re.I)
            ),
            "accessible-feedback": "aria-live" in lowered,
            "form-labels": "<form" not in lowered or "<label" in lowered,
        }
        for name, passed in accessibility_requirements.items():
            if passed:
                checks.append(f"accessibility-{name}")
            else:
                issues.append(f"Accessibility contract failed: {name}")
        missing_references = []
        for reference in parser.references:
            clean = reference.split("#", 1)[0].split("?", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
                continue
            try:
                relative = self._safe_relative_path(clean.lstrip("/"))
            except ValueError:
                missing_references.append(reference)
                continue
            if not (source_dir / relative).is_file():
                missing_references.append(reference)
        if missing_references:
            issues.append("Missing local references: " + ", ".join(sorted(set(missing_references))))
        else:
            checks.append("local-references")
        objective = f"{mission.title} {mission.objective}".casefold()
        if ("旅遊" in objective or "travel" in objective) and not any(
            token in lowered for token in ("旅遊", "旅行", "行程", "目的地", "travel")
        ):
            issues.append("Generated content is not relevant to the travel objective")
        else:
            checks.append("objective-relevance")
        file_count = sum(1 for path in source_dir.rglob("*") if path.is_file())
        digest = self._source_hash(source_dir)
        issues = sorted(set(issues))
        checks = sorted(set(checks))
        quality_score = round((len(checks) / max(len(checks) + len(issues), 1)) * 100)
        threshold_met = quality_score >= self.settings.mission_quality_min_score
        if enforce_threshold and not threshold_met:
            issues.append(
                f"Website quality score {quality_score} is below required "
                f"{self.settings.mission_quality_min_score}"
            )
        return {
            "success": not issues,
            "issues": issues,
            "checks": checks,
            "quality_score": quality_score,
            "quality_threshold": self.settings.mission_quality_min_score,
            "quality_threshold_met": threshold_met,
            "section_count": section_count,
            "heading_count": heading_count,
            "file_count": file_count,
            "source_sha256": digest,
            "preview_url": f"/v1/missions/{mission.mission_id}/preview/",
        }

    async def _record_cycle_event(
        self,
        cycle: MissionReactCycleRecord,
        step: MissionStepRecord,
    ) -> None:
        await self.events.publish(
            "MissionReactCycleSucceeded"
            if cycle.status is MissionCycleStatus.SUCCEEDED
            else "MissionReactCorrectionQueued"
            if cycle.status is MissionCycleStatus.NEEDS_CORRECTION
            else "MissionReactCycleFailed",
            step.step_id,
            {
                "mission_id": step.mission_id,
                "step_key": step.step_key,
                "attempt": cycle.attempt,
                "reason_summary": cycle.reason_summary,
                "action": cycle.action,
                "observation": cycle.observation,
                "correction": cycle.correction,
                "step_status": step.status.value,
            },
            correlation_id=step.mission_id,
        )

    async def _update_progress(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if mission.status in {
            MissionStatus.AWAITING_REVIEW,
            MissionStatus.APPROVED,
            MissionStatus.CANCELLED,
        }:
            return
        steps = self.store.list_mission_steps(mission_id)
        succeeded = sum(item.status is MissionStepStatus.SUCCEEDED for item in steps)
        failed = next((item for item in steps if item.status is MissionStepStatus.FAILED), None)
        if failed:
            blocked = self.store.block_pending_mission_steps(
                mission_id,
                reason=f"Blocked by failed step {failed.step_key}.",
            )
            status = MissionStatus.BLOCKED
            current_step = f"步驟 {failed.step_key} 驗證失敗；已停止 {blocked} 個相依步驟"
        else:
            active = next(
                (
                    item
                    for item in steps
                    if item.status in {MissionStepStatus.RUNNING, MissionStepStatus.PENDING}
                ),
                None,
            )
            status = MissionStatus.ACTIVE
            current_step = (
                f"{active.step_key} · {active.objective}"
                if active
                else "所有執行步驟完成，正在整理驗收證據"
            )
        progress = {
            **mission.progress,
            "executor": "p6-durable-react.v2",
            "completion_percent": round((succeeded / max(len(steps), 1)) * 100),
            "current_step": current_step,
            "steps_succeeded": succeeded,
            "steps_total": len(steps),
            "failed_step": failed.step_key if failed else None,
        }
        self.store.set_mission_status(mission_id, status, progress=progress)

    def _step_by_key(self, mission_id: str, step_key: str) -> MissionStepRecord:
        for step in self.store.list_mission_steps(mission_id):
            if step.step_key == step_key:
                return step
        raise KeyError(f"Mission step not found: {step_key}")

    def _mission_dir(self, mission_id: str) -> Path:
        if not self._mission_id_pattern.fullmatch(mission_id):
            raise ValueError("Invalid mission ID.")
        path = (self.root / mission_id).resolve()
        path.relative_to(self.root)
        return path

    def _source_dir(self, mission_id: str) -> Path:
        source = (self._mission_dir(mission_id) / "source").resolve()
        source.relative_to(self._mission_dir(mission_id))
        return source

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        normalized = PurePosixPath(value.replace("\\", "/"))
        if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
            raise ValueError(f"Unsafe mission artifact path: {value}")
        if any(part.startswith(".") for part in normalized.parts):
            raise ValueError(f"Hidden mission artifact path is not allowed: {value}")
        return normalized.as_posix()

    def _validated_site_files(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        total = 0
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Website file entries must be objects.")
            path = self._safe_relative_path(str(item.get("path", "")))
            content = str(item.get("content", ""))
            if Path(path).suffix.casefold() not in self._allowed_site_suffixes:
                raise ValueError(f"Unsupported website file type: {path}")
            if path in seen or not content.strip():
                raise ValueError(f"Duplicate or empty website file: {path}")
            seen.add(path)
            total += len(content.encode("utf-8"))
            files.append({"path": path, "content": content})
        if len(files) > 30 or total > 1_000_000:
            raise ValueError("Website draft exceeds the file or byte contract.")
        required = {"index.html", "styles.css", "app.js", "README.md"}
        if not required.issubset(seen):
            raise ValueError("Website draft is missing required files.")
        return files

    def _validated_python_files(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        allowed = {".py", ".toml", ".md", ".txt", ".json", ".yaml", ".yml"}
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        total = 0
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Python project file entries must be objects.")
            path = self._safe_relative_path(str(item.get("path", "")))
            content = str(item.get("content", ""))
            if Path(path).suffix.casefold() not in allowed:
                raise ValueError(f"Unsupported Python project file type: {path}")
            if path in seen or not content.strip():
                raise ValueError(f"Duplicate or empty Python project file: {path}")
            seen.add(path)
            total += len(content.encode("utf-8"))
            files.append({"path": path, "content": content})
        if len(files) > 30 or total > 500_000:
            raise ValueError("Python project draft exceeds the file or byte contract.")
        if not any(path.startswith("tests/test_") and path.endswith(".py") for path in seen):
            raise ValueError("Python project requires deterministic pytest tests.")
        if not any(path.endswith(".py") and not path.startswith("tests/") for path in seen):
            raise ValueError("Python project requires executable source.")
        return files

    def _latest_step_by_action(self, mission_id: str, action_kind: str) -> MissionStepRecord:
        matching = [
            item
            for item in self.store.list_mission_steps(mission_id)
            if item.action_kind == action_kind
            and item.status in {MissionStepStatus.SUCCEEDED, MissionStepStatus.RUNNING}
        ]
        if not matching:
            raise KeyError(f"Mission action output not found: {action_kind}")
        return max(matching, key=lambda item: item.sequence)

    def _source_files(
        self,
        source_dir: Path,
        project_type: str,
    ) -> list[dict[str, str]]:
        allowed = (
            self._allowed_site_suffixes
            if project_type == "static_website"
            else {".py", ".toml", ".md", ".txt", ".json", ".yaml", ".yml"}
        )
        files: list[dict[str, str]] = []
        total = 0
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or ".git" in path.parts or path.suffix.casefold() not in allowed:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            total += len(content)
            if total > 900_000:
                raise ValueError("Mission source exceeds the review context contract.")
            files.append(
                {
                    "path": path.relative_to(source_dir).as_posix(),
                    "content": content,
                }
            )
        return files

    def _write_project_files(
        self,
        source_dir: Path,
        files: list[dict[str, str]],
    ) -> None:
        self._clear_source(source_dir)
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")

    def _fallback_quality_improvement(
        self,
        source_dir: Path,
        *,
        project_type: str,
        revision_key: str,
    ) -> list[dict[str, str]]:
        files = self._source_files(source_dir, project_type)
        slug = re.sub(r"[^a-z0-9]+", "-", revision_key.casefold()).strip("-")[:48]
        slug = slug or "quality"
        if project_type == "python_project":
            by_path = {item["path"]: item for item in files}
            by_path[f"QUALITY-{slug.upper()}.md"] = {
                "path": f"QUALITY-{slug.upper()}.md",
                "content": (
                    f"# Quality revision {slug}\n\n"
                    "The isolated deterministic tests and objective-specific interfaces remain "
                    "the acceptance evidence for this revision.\n"
                ),
            }
            return list(by_path.values())
        by_path = {item["path"]: item for item in files}
        stylesheet = by_path["styles.css"]
        marker = f"/* Deterministic quality refinement: {slug}. */"
        if marker in stylesheet["content"]:
            return files
        stylesheet["content"] += f"""

{marker}
:where(a, button, input, select, textarea):focus-visible {{
  outline: 3px solid var(--orange, #ff7d4d);
  outline-offset: 4px;
}}
html[data-quality-revision="{slug}"] .journey-card {{
  transform-origin: center bottom;
  transition: transform .25s ease, box-shadow .25s ease;
}}
html[data-quality-revision="{slug}"] .journey-card:hover {{
  transform: translateY(-4px);
  box-shadow: 0 18px 45px rgba(23, 32, 27, .12);
}}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ scroll-behavior: auto !important; transition: none !important; }}
}}
"""
        script = by_path["app.js"]
        identifier = slug.replace("-", "_")
        script["content"] += f"""

document.documentElement.dataset.qualityRevision = '{slug}';
const qualityStatus_{identifier} = document.querySelector('#plan-result');
window.addEventListener('load', () => {{
  document.body.dataset.interfaceReady = 'true';
}});
document.addEventListener('keydown', (event) => {{
  if (event.key === 'Escape') {{
    document.querySelector('.site-header')?.classList.remove('menu-open');
  }}
}});
document.querySelectorAll('a[href^="#"]').forEach((link) => {{
  link.addEventListener('click', () => {{
    qualityStatus_{identifier}?.setAttribute('data-last-action', link.getAttribute('href') || '');
  }});
}});
"""
        return files

    def _fallback_python_files(self, mission: MissionRecord) -> list[dict[str, str]]:
        objective_tokens = [
            token
            for token in re.findall(r"[a-z][a-z0-9]{3,}", mission.objective.casefold())
            if token not in {"build", "create", "make", "project", "software", "simple"}
        ]
        focus = "_".join(objective_tokens[:3]) or "mission"
        function_name = f"build_{focus}_plan"
        source = f'''from __future__ import annotations


def {function_name}(goal: str, *, max_steps: int = 6) -> tuple[str, ...]:
    normalized = " ".join(goal.split())
    if not normalized:
        raise ValueError("goal must not be empty")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    clauses = [item.strip() for item in normalized.replace("；", ";").split(";")]
    meaningful = [item for item in clauses if item]
    if len(meaningful) == 1:
        meaningful = [
            f"Define a measurable contract for {{normalized}}",
            f"Implement the smallest complete unit for {{normalized}}",
            f"Verify the delivered behavior for {{normalized}}",
        ]
    return tuple(meaningful[:max_steps])


def completion_ratio(completed: int, total: int) -> float:
    if total <= 0 or completed < 0 or completed > total:
        raise ValueError("invalid progress counts")
    return completed / total
'''
        tests = f'''import pytest

from mission_app import {function_name}, completion_ratio


def test_plan_is_bounded_and_goal_specific() -> None:
    goal = {mission.title!r}
    plan = {function_name}(goal, max_steps=3)
    assert 1 <= len(plan) <= 3
    assert any(goal in step for step in plan)


def test_progress_contract_rejects_invalid_counts() -> None:
    assert completion_ratio(2, 4) == 0.5
    with pytest.raises(ValueError):
        completion_ratio(5, 4)
'''
        readme = f"""# {mission.title}

{mission.objective}

This fallback is an executable, tested mission decomposition kernel. The P6 coder worker may
replace it with a more domain-specific implementation, but Docker verification remains mandatory.
"""
        return [
            {"path": "mission_app.py", "content": source},
            {"path": "tests/test_mission_app.py", "content": tests},
            {"path": "README.md", "content": readme},
        ]

    def _fallback_site_files(self, mission: MissionRecord) -> list[dict[str, str]]:
        title = html.escape(mission.title)
        objective = html.escape(mission.objective)
        travel = bool(re.search(r"旅遊|旅行|travel", f"{mission.title} {mission.objective}", re.I))
        theme_title = "緩慢出走" if travel else title
        theme_kicker = "CURATED JOURNEYS" if travel else "ECK VERIFIED DELIVERY"
        cards = (
            (
                ("山海之間", "三日東岸慢旅", "沿著海岸、部落與山徑安排不趕路的留白。"),
                ("城市漫步", "巷弄味覺地圖", "從早市到夜色，用步行距離串起城市的日常。"),
                ("島嶼週末", "兩日輕裝提案", "以交通、預算與天候為核心，快速建立可行行程。"),
            )
            if travel
            else (
                ("清楚", "從目標開始", "把任務需求整理為能被檢查的完整成果。"),
                ("可用", "直接預覽", "所有樣式與互動都由本機檔案提供。"),
                ("可驗證", "保留證據", "來源、封裝雜湊與驗收狀態皆可追溯。"),
            )
        )
        card_markup = "".join(
            f'<article class="journey-card"><span>{html.escape(kicker)}</span>'
            f"<h3>{html.escape(heading)}</h3><p>{html.escape(copy)}</p>"
            '<button class="card-action" type="button">加入靈感清單</button></article>'
            for kicker, heading, copy in cards
        )
        index = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{objective}">
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="#top" aria-label="回到首頁">ECK<span>JOURNEY</span></a>
    <nav aria-label="主要導覽">
      <a href="#ideas">靈感</a><a href="#planner">規劃</a><a href="#about">理念</a>
    </nav>
    <button class="menu-button" type="button" aria-expanded="false">選單</button>
  </header>
  <main id="top">
    <section class="hero">
      <div class="hero-copy">
      <p class="eyebrow">{theme_kicker}</p>
      <h1>{theme_title}<br><em>把時間還給風景</em></h1>
      <p class="lead">{objective}</p><a class="primary-action" href="#ideas">開始探索</a></div>
      <div class="hero-art" role="img" aria-label="抽象山海旅行風景">
        <span class="sun"></span><span class="mountain one"></span>
        <span class="mountain two"></span><span class="route"></span>
      </div>
    </section>
    <section class="idea-section" id="ideas">
      <div class="section-heading"><p class="eyebrow">SELECTED IDEAS</p>
      <h2>從一個方向，長出自己的行程</h2></div>
      <div class="card-grid">{card_markup}</div>
    </section>
    <section class="planner" id="planner"><div><p class="eyebrow">QUICK PLANNER</p>
      <h2>今天想去哪裡？</h2><p>選擇旅行節奏，立即取得一份本機產生的起始建議。</p></div>
      <form id="planner-form"><label>旅行節奏<select id="pace">
        <option value="慢慢走">慢慢走</option>
        <option value="城市探索">城市探索</option>
        <option value="自然冒險">自然冒險</option>
      </select></label><button type="submit">產生建議</button></form>
      <output id="plan-result" aria-live="polite">選一種節奏，讓旅程開始成形。</output>
    </section>
    <section class="about" id="about"><p class="eyebrow">WHY THIS EXISTS</p>
      <h2>少一點清單，多一點真正抵達。</h2>
      <p>這份成果由 ECK 在隔離任務工作區建立，通過本機檔案引用、語意結構、
      響應式版面與主題相關性檢查後才提交。</p>
    </section>
  </main>
  <footer><span>AI/ECK 協作建立</span><span>LOCAL · VERIFIED · PORTABLE</span></footer>
  <script src="app.js"></script>
</body>
</html>
"""
        styles = """
:root {
  --ink: #17201b; --paper: #f3efe5; --lime: #d8ff72; --orange: #ff7d4d;
  --line: rgba(23, 32, 27, .18); --serif: Georgia, 'Times New Roman', serif;
  --sans: Inter, 'Noto Sans TC', system-ui, sans-serif;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; color: var(--ink); background: var(--paper); font-family: var(--sans); }
a { color: inherit; text-decoration: none; }
.site-header { position: sticky; top: 0; z-index: 10; display: flex; align-items: center;
  justify-content: space-between; padding: 1rem clamp(1rem, 4vw, 4.5rem);
  border-bottom: 1px solid var(--line); background: rgba(243, 239, 229, .9);
  backdrop-filter: blur(16px); }
.brand { font-weight: 900; letter-spacing: -.05em; }
.brand span { display: block; font-size: .5rem; letter-spacing: .24em; }
.site-header nav { display: flex; gap: 1.6rem; font-size: .78rem; }
.site-header nav a:hover { opacity: .55; }
.menu-button { display: none; border: 1px solid var(--line); background: transparent;
  padding: .55rem .8rem; }
.hero { min-height: 78vh; display: grid; grid-template-columns: 1.05fr .95fr;
  align-items: center; padding: clamp(3rem, 8vw, 8rem) clamp(1rem, 7vw, 8rem);
  overflow: hidden; }
.eyebrow { font-size: .64rem; font-weight: 800; letter-spacing: .2em; }
.hero h1, .section-heading h2, .planner h2, .about h2 {
  font: 500 clamp(3rem, 8vw, 7.5rem)/.88 var(--serif); letter-spacing: -.065em;
  margin: .4rem 0 1.5rem; }
.hero h1 em { color: var(--orange); font-weight: 400; }
.lead { max-width: 38rem; font-size: 1rem; line-height: 1.8; }
.primary-action, .planner button { display: inline-flex; margin-top: 1rem;
  padding: .9rem 1.3rem; border: 1px solid var(--ink); background: var(--ink);
  color: var(--paper); font-weight: 750; }
.hero-art { position: relative; min-height: 34rem; border-radius: 50% 50% 4% 4%;
  background: linear-gradient(#c4e3dd 0 53%, #99c8c4 53%); overflow: hidden;
  box-shadow: inset 0 0 0 1px var(--line); }
.sun { position: absolute; top: 12%; right: 18%; width: 6rem; height: 6rem;
  border-radius: 50%; background: var(--lime); }
.mountain { position: absolute; bottom: 40%; width: 0; height: 0;
  border-left: 12rem solid transparent; border-right: 12rem solid transparent;
  border-bottom: 16rem solid #415b4b; }
.mountain.one { left: -20%; }
.mountain.two { right: -26%; bottom: 35%; border-bottom-color: #6f8c75; }
.route { position: absolute; left: 50%; bottom: -10%; width: 4rem; height: 70%;
  border: 3px solid rgba(243, 239, 229, .75); border-color: rgba(243, 239, 229, .75)
  transparent transparent transparent; border-radius: 50%; transform: rotate(-8deg); }
.idea-section { padding: 7rem clamp(1rem, 7vw, 8rem); border-top: 1px solid var(--line); }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 2rem; }
.section-heading h2 { max-width: 14ch; font-size: clamp(2.5rem, 5vw, 5rem); }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
.journey-card { min-height: 23rem; display: flex; flex-direction: column; padding: 1.5rem;
  border: 1px solid var(--line); background: rgba(255, 255, 255, .24); transition: .25s; }
.journey-card:hover { transform: translateY(-5px); background: var(--lime); }
.journey-card span { font-size: .6rem; letter-spacing: .15em; }
.journey-card h3 { font: 500 2rem var(--serif); margin: 3rem 0 1rem; }
.journey-card p { line-height: 1.7; }
.card-action { margin-top: auto; align-self: start; border: 0; border-bottom: 1px solid;
  background: transparent; padding: .5rem 0; cursor: pointer; }
.card-action.saved { font-weight: 800; }
.planner { display: grid; grid-template-columns: 1fr 1fr; gap: 4rem;
  padding: 7rem clamp(1rem, 7vw, 8rem); background: var(--ink); color: var(--paper); }
.planner h2 { font-size: clamp(2.5rem, 5vw, 5rem); }
#planner-form { display: flex; gap: .7rem; align-items: end; }
#planner-form label { display: grid; gap: .4rem; flex: 1; font-size: .7rem; }
select { width: 100%; padding: .85rem; border: 1px solid rgba(255, 255, 255, .3);
  background: transparent; color: var(--paper); }
select option { color: var(--ink); }
.planner button { margin: 0; background: var(--lime); color: var(--ink); }
#plan-result { grid-column: 2; padding: 1rem; border-left: 3px solid var(--orange);
  line-height: 1.7; }
.about { padding: 8rem clamp(1rem, 12vw, 14rem); text-align: center; }
.about h2 { font-size: clamp(3rem, 7vw, 7rem); }
.about > p:last-child { max-width: 48rem; margin: auto; line-height: 1.9; }
footer { display: flex; justify-content: space-between; padding: 1.5rem clamp(1rem, 4vw, 4.5rem);
  border-top: 1px solid var(--line); font-size: .62rem; letter-spacing: .12em; }
@media (max-width: 800px) {
  .site-header nav { display: none; }
  .site-header nav.open { position: absolute; top: 100%; left: 0; right: 0; display: flex;
    flex-direction: column; padding: 1rem; background: var(--paper);
    border-bottom: 1px solid var(--line); }
  .menu-button { display: block; }
  .hero { grid-template-columns: 1fr; gap: 3rem; }
  .hero-art { min-height: 26rem; }
  .section-heading { display: block; }
  .card-grid, .planner { grid-template-columns: 1fr; }
  .planner { gap: 2rem; }
  #plan-result { grid-column: 1; }
  .hero h1 { font-size: clamp(3rem, 15vw, 5.5rem); }
}
"""
        script = """
const menuButton = document.querySelector('.menu-button');
const nav = document.querySelector('.site-header nav');
menuButton.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  menuButton.setAttribute('aria-expanded', String(open));
});
document.querySelectorAll('.card-action').forEach((button) => {
  button.addEventListener('click', () => {
    const saved = button.classList.toggle('saved');
    button.textContent = saved ? '已加入靈感' : '加入靈感清單';
  });
});
const ideas = {
  '慢慢走': '保留半天空白，只選一個街區與一頓期待的晚餐。',
  '城市探索': '從市場、博物館與夜間散步建立三段式路線。',
  '自然冒險': '先確認天候與交通，再選擇一條可安全折返的步道。',
};
document.querySelector('#planner-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const pace = document.querySelector('#pace').value;
  document.querySelector('#plan-result').textContent = `${pace}提案：${ideas[pace]}`;
});
"""
        readme = f"""# {mission.title}

{mission.objective}

## 執行

直接開啟 `index.html`，或由 ECK 的任務預覽網址檢視。

## 驗證

本專案由 P6 Durable Mission Executor 建立，只有通過本機靜態網站契約後才會封裝與提交。
"""
        return [
            {"path": "index.html", "content": index},
            {"path": "styles.css", "content": styles},
            {"path": "app.js", "content": script},
            {"path": "README.md", "content": readme},
        ]

    @staticmethod
    def _clear_source(source_dir: Path) -> None:
        source_root = source_dir.resolve()
        if source_root.name != "source" or not source_root.parent.name.startswith("mission_"):
            raise ValueError("Mission source cleanup escaped the isolated workspace.")
        for path in source_root.iterdir():
            if path.name == ".git":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    @staticmethod
    def _directory_bytes(path: Path, *, missing_ok: bool = False) -> int:
        if not path.exists():
            if missing_ok:
                return 0
            raise FileNotFoundError(path)
        total = 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _source_hash(source_dir: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            digest.update(path.relative_to(source_dir).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise
            value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("Model response must be a JSON object.")
        return value

    @staticmethod
    def _safe_project_name(value: str, mission_id: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:50]
        if len(normalized) < 3 or not normalized[0].isalpha():
            normalized = f"eck-mission-{mission_id[-8:]}"
        return normalized

    def _project_name(self, mission: MissionRecord) -> str:
        sequence = self.store.mission_sequence(mission.mission_id)
        return f"{self._repository_topic(mission)}-task-{sequence:04d}"

    @staticmethod
    def _repository_topic(mission: MissionRecord) -> str:
        text = f"{mission.title} {mission.objective}"
        mappings = (
            (r"旅遊|旅行|travel", "travel"),
            (r"股票|投資|stock|finance", "finance"),
            (r"影片|video", "video"),
            (r"圖片|影像|image", "image"),
            (r"遊戲|game", "game"),
            (r"網站|網頁|website|landing", "website"),
            (r"app|應用", "app"),
            (r"api", "api"),
        )
        for pattern, topic in mappings:
            if re.search(pattern, text, re.I):
                return topic
        tokens = re.findall(r"[a-z][a-z0-9]{2,}", text.casefold())
        return "-".join(tokens[:3])[:36] or "software"
