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
        definitions = (
            MissionStepDefinition(
                step_key="workspace.prepare",
                sequence=10,
                action_kind="workspace.prepare",
                objective="建立與 ECK 核心隔離、受容量配額約束的任務工作區。",
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="software.specify",
                sequence=20,
                action_kind="software.specify",
                objective="把使用者目標編譯為可驗證的軟體規格與驗收條件。",
                depends_on=("workspace.prepare",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="software.implement",
                sequence=30,
                action_kind="software.implement",
                objective="依規格建立完整可執行來源檔，不以文字說明冒充成果。",
                depends_on=("software.specify",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="software.validate",
                sequence=40,
                action_kind="software.validate",
                objective="執行確定性驗證；失敗時根據真實觀察修正後重測。",
                depends_on=("software.implement",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="artifact.package",
                sequence=50,
                action_kind="artifact.package",
                objective="封裝通過驗證的來源並產生 SHA-256 可追溯證據。",
                depends_on=("software.validate",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="github.publish",
                sequence=60,
                action_kind="github.publish",
                objective="使用 ECK 專用帳號把已驗證來源推送至私有 GitHub 儲存庫。",
                depends_on=("artifact.package",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
            MissionStepDefinition(
                step_key="mission.submit",
                sequence=70,
                action_kind="mission.submit",
                objective="提交預覽、封裝、驗證與 GitHub 證據，等待建立者驗收。",
                depends_on=("github.publish",),
                inputs={"project_type": project_type},
                max_attempts=attempts,
            ),
        )
        steps = self.store.create_mission_steps(mission_id, definitions)
        progress = {
            **mission.progress,
            "execution_kind": "software_project",
            "executor": "p6-durable-react.v1",
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
                "executor": "p6-durable-react.v1",
                "project_type": project_type,
                "step_count": len(steps),
            },
            correlation_id=mission_id,
        )
        return steps

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
            "executor": "p6-durable-react.v1",
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
            "software.specify": self._specify_software,
            "software.implement": self._implement_software,
            "software.validate": self._validate_software,
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
        spec = self._step_by_key(mission.mission_id, "software.specify").output
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
                            "/no_think\n你是世界級前端工程師。只輸出 JSON。直接交付完整網站檔案，"
                            "不是範例、教學或程式碼片段。必須包含 index.html、styles.css、"
                            "app.js、README.md；不可使用 CDN、外部圖片、框架、TODO 或 Lorem ipsum。"
                            "所有內容需符合任務主題，具響應式排版、可操作互動與無障礙基本標記。"
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
                options={"temperature": 0.25, "num_predict": 8192},
            )
            files = self._validated_site_files(self._json_object(response.content).get("files"))
            model = response.model
        except (json.JSONDecodeError, RuntimeError, ValueError):
            files = []
        if not files:
            files = self._fallback_site_files(mission)
        source_dir = self._source_dir(mission.mission_id)
        self._clear_source(source_dir)
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
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

    async def _validate_software(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
    ) -> StepOutcome:
        source_dir = self._source_dir(mission.mission_id)
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
        report = self._validate_site(source_dir, mission)
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

    async def _package_artifact(
        self,
        mission: MissionRecord,
        _: MissionStepRecord,
    ) -> StepOutcome:
        validation = self._step_by_key(mission.mission_id, "software.validate")
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
        spec = self._step_by_key(mission.mission_id, "software.specify").output
        base_name = self._safe_project_name(
            str(spec.get("project_name", self._project_name(mission))), mission.mission_id
        )
        repository_name = f"{base_name}-{mission.mission_id[-8:]}"
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
        validation = self._step_by_key(mission.mission_id, "software.validate").output
        package = self._step_by_key(mission.mission_id, "artifact.package").output
        github = self._step_by_key(mission.mission_id, "github.publish").output
        project_type = str(
            self._step_by_key(mission.mission_id, "software.specify").inputs.get(
                "project_type", "static_website"
            )
        )
        preview_url = (
            f"/v1/missions/{mission.mission_id}/preview/"
            if project_type == "static_website"
            else ""
        )
        evidence = [
            preview_url,
            str(package.get("download_url", "")),
            f"sha256:{package.get('sha256', '')}",
        ]
        if github.get("url"):
            evidence.append(str(github["url"]))
        evidence = [item for item in evidence if item and not item.endswith(":")]
        summary = (
            "P6 已完成隔離工作區、完整來源、確定性網站驗證與可追溯封裝。"
            f"驗證通過 {len(validation.get('checks', []))} 項；"
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

    def _validate_site(self, source_dir: Path, mission: MissionRecord) -> dict[str, Any]:
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
        if not re.search(r"<meta[^>]+name=[\"']viewport[\"']", markup, re.I):
            issues.append("Responsive viewport metadata is missing")
        else:
            checks.append("responsive-viewport")
        lowered = markup.casefold()
        if any(token in lowered for token in ("lorem ipsum", "todo", "coming soon")):
            issues.append("Placeholder content remains in the deliverable")
        else:
            checks.append("no-placeholder-content")
        if not css.is_file() or css.stat().st_size < 400:
            issues.append("styles.css is missing or too small to represent a complete layout")
        elif "styles.css" not in parser.references:
            issues.append("index.html does not reference styles.css")
        else:
            stylesheet = css.read_text(encoding="utf-8", errors="replace")
            if stylesheet.count("{") != stylesheet.count("}"):
                issues.append("CSS braces are unbalanced")
            else:
                checks.append("local-css")
        if not script.is_file() or script.stat().st_size < 120:
            issues.append("app.js is missing or has no meaningful interaction")
        elif "app.js" not in parser.references:
            issues.append("index.html does not reference app.js")
        else:
            checks.append("local-javascript")
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
        return {
            "success": not issues,
            "issues": issues,
            "checks": checks,
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
            "executor": "p6-durable-react.v1",
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
        if re.search(r"旅遊|旅行|travel", f"{mission.title} {mission.objective}", re.I):
            return f"eck-travel-site-{mission.mission_id[-8:]}"
        return f"eck-mission-site-{mission.mission_id[-8:]}"
