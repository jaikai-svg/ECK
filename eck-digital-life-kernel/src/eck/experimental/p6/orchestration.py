from __future__ import annotations

import hashlib
import json
import zipfile
from typing import Any

from eck.core.time import utc_now
from eck.domain.enums import MissionStepStatus
from eck.domain.models import MissionCompletionCreate, MissionRecord, MissionStepRecord
from eck.experimental.p6.executor_base import MissionExecutorMixinBase, StepOutcome


class MissionSoftwareOrchestrationMixin(MissionExecutorMixinBase):
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
        quality_gate: dict[str, Any] | None = None
        if model != "deterministic-site-fallback.v1":
            candidate_files = files
            self._write_project_files(source_dir, candidate_files)
            candidate_report = self._validate_site(
                source_dir,
                mission,
                enforce_threshold=False,
            )
            baseline_files = self._fallback_site_files(mission)
            self._write_project_files(source_dir, baseline_files)
            baseline_report = self._validate_site(
                source_dir,
                mission,
                enforce_threshold=False,
            )
            if self._site_quality_rank(candidate_report) >= self._site_quality_rank(
                baseline_report
            ):
                files = candidate_files
                self._write_project_files(source_dir, files)
                quality_gate = {
                    "selected": "model",
                    "candidate_score": candidate_report["quality_score"],
                    "baseline_score": baseline_report["quality_score"],
                }
            else:
                files = baseline_files
                model = "deterministic-site-fallback.v1"
                quality_gate = {
                    "selected": "verified-baseline",
                    "candidate_score": candidate_report["quality_score"],
                    "baseline_score": baseline_report["quality_score"],
                    "candidate_issues": candidate_report["issues"],
                }
        else:
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
                "quality_gate": quality_gate,
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
        before_files = self._source_files(source_dir, project_type)
        before_quality = (
            self._validate_site(source_dir, mission, enforce_threshold=False)
            if project_type == "static_website"
            else None
        )
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
        rejected_candidate: dict[str, Any] | None = None
        if project_type == "static_website" and before_quality is not None:
            candidate_quality = self._validate_site(
                source_dir,
                mission,
                enforce_threshold=False,
            )
            if self._site_quality_rank(candidate_quality) < self._site_quality_rank(
                before_quality
            ):
                rejected_candidate = {
                    "quality_score": candidate_quality["quality_score"],
                    "issues": candidate_quality["issues"],
                }
                self._write_project_files(source_dir, before_files)
                files = self._fallback_quality_improvement(
                    source_dir,
                    project_type=project_type,
                    revision_key=revision_key,
                )
                result["model"] = "deterministic-non-degrading-improvement.v1"
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
            "rejected_degrading_candidate": rejected_candidate,
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

