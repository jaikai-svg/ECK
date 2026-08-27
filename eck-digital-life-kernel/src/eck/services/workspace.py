from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eck.core.time import iso_now
from eck.domain.enums import MissionStatus, MissionStepStatus, TaskStatus
from eck.domain.models import MissionRecord
from eck.modules.skills.lifecycle import (
    SkillLifecycleItem,
    SkillLifecyclePhase,
    SkillSourceKind,
)

if TYPE_CHECKING:
    from eck.app import Application


class WorkspaceReadService:
    """Output-focused projections over ECK's existing authoritative stores."""

    _active_mission_statuses = (
        MissionStatus.ACTIVE,
        MissionStatus.PREPARING,
        MissionStatus.BLOCKED,
        MissionStatus.AWAITING_REVIEW,
        MissionStatus.REJECTED,
    )
    _result_mission_statuses = (
        MissionStatus.AWAITING_REVIEW,
        MissionStatus.APPROVED,
    )

    def __init__(self, application: Application) -> None:
        self.application = application

    def home(self) -> dict[str, Any]:
        kernel = self.application.kernel.status()
        lifecycle = self.application.skill_lifecycle.list(limit=2000)
        active_memory_skills = sum(
            item.active and item.source_kind is SkillSourceKind.EXPERIENCE
            for item in lifecycle
        )
        active_runtime_skills = sum(
            item.active and item.source_kind is SkillSourceKind.RUNTIME
            for item in lifecycle
        )
        missions = self.application.store.list_missions_page(
            limit=6,
            offset=0,
            statuses=self._active_mission_statuses,
        )
        executions = {
            mission.mission_id: self._execution_item(mission.mission_id)
            for mission in missions[:4]
        }

        tasks = self.application.store.list_tasks(
            statuses=(
                TaskStatus.RUNNING,
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.QUEUED,
            ),
            limit=8,
        )
        results = self.application.store.list_missions_page(
            limit=6,
            offset=0,
            statuses=self._result_mission_statuses,
        )
        activity = self._current_activity(tasks, missions, executions)
        resources = self.application.resources.quick_snapshot()
        busy = activity["state"] in {"running", "queued", "waiting", "blocked"}
        return {
            "schema_version": "eck-workspace-home.v1",
            "generated_at": iso_now(),
            "kernel": kernel.model_dump(mode="json"),
            "activity": activity,
            "running_projects": [
                self._project_summary(item, executions.get(item.mission_id))
                for item in missions[:4]
            ],
            "recent_results": [self._project_summary(item) for item in results],
            "learning": {
                "verified_experiences": self.application.store.count_experiences(
                    admitted=True
                ),
                "knowledge_items": self.application.store.count_knowledge(),
                "memory_skills": active_memory_skills,
                "runtime_skills": active_runtime_skills,
                "available_skills": active_memory_skills + active_runtime_skills,
                "total_memory_skills": self.application.store.count_skills(),
                "total_runtime_skills": len(
                    self.application.store.list_runtime_skills(limit=2000)
                ),
            },
            "resources": {
                "sampled_at": resources.get("sampled_at"),
                "pressure": resources.get("pressure", {}),
                "process": resources.get("process", {}),
                "host": resources.get("host", {}),
            },
            "refresh": {
                "busy": busy,
                "poll_after_seconds": 5 if busy else 30,
                "pause_when_hidden": True,
            },
        }

    def system(self) -> dict[str, Any]:
        resources = self.application.resources.quick_snapshot()
        resources["project"] = self.application.resources.cached_project_snapshot()
        return {
            "schema_version": "eck-workspace-system.v1",
            "generated_at": iso_now(),
            "services": {
                **self.application.local_services.status(),
                "forge": self.application.image_generation.status(),
            },
            "evolution": self.application.evolution_transactions.status(),
            "resources": resources,
            "project_measurement_policy": (
                "Workspace reads the latest cached project measurement and never starts a "
                "recursive filesystem scan during page rendering."
            ),
        }

    def projects(
        self,
        *,
        limit: int,
        offset: int,
        status: MissionStatus | None,
    ) -> dict[str, Any]:
        statuses = (status,) if status is not None else None
        missions = self.application.store.list_missions_page(
            limit=limit,
            offset=offset,
            statuses=statuses,
        )
        total = self.application.store.count_missions(statuses=statuses)
        items = [self._project_summary(item) for item in missions]
        next_offset = offset + len(items)
        return {
            "schema_version": "eck-workspace-project-page.v1",
            "items": items,
            "page": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "next_offset": next_offset if next_offset < total else None,
            },
        }

    def project(self, mission_id: str) -> dict[str, Any]:
        mission = self.application.store.get_mission(mission_id)
        revisions = self.application.store.list_mission_revisions(mission_id)
        execution = self._execution_item(mission_id)
        steps = list(execution.get("steps", [])) if execution else []
        cycles = list(execution.get("cycles", [])) if execution else []
        step_by_id = {str(item.get("step_id")): item for item in steps}
        timeline = []
        for cycle in cycles:
            step = step_by_id.get(str(cycle.get("step_id")), {})
            timeline.append(
                {
                    "cycle_id": cycle.get("cycle_id"),
                    "attempt": cycle.get("attempt"),
                    "goal": mission.objective,
                    "plan": cycle.get("reason_summary") or step.get("objective", ""),
                    "action": cycle.get("action", {}),
                    "observation": cycle.get("observation", {}),
                    "correction": cycle.get("correction", ""),
                    "verification": cycle.get("status", ""),
                    "conclusion": (
                        mission.result_summary
                        if mission.status is MissionStatus.APPROVED
                        else ""
                    ),
                    "created_at": cycle.get("created_at"),
                }
            )
        project = self._project_summary(mission, execution)
        project["edit_revision_count"] = len(revisions)
        return {
            "schema_version": "eck-workspace-project.v1",
            "project": project,
            "mission": mission.model_dump(mode="json"),
            "edit_revisions": revisions,
            "steps": steps,
            "react_summaries": timeline,
            "artifacts": self._artifact_links(mission),
            "skill_usages": self.application.store.list_task_skill_usages(
                project_id=mission_id,
                limit=500,
            ),
            "workspace_bytes": execution.get("workspace_bytes", 0) if execution else 0,
            "thinking_policy": (
                "Only goal, plan, action, observation, correction, verification, and "
                "conclusion summaries are exposed. Private chain-of-thought is not stored."
            ),
        }

    def skills(
        self,
        *,
        limit: int,
        offset: int,
        phase: SkillLifecyclePhase | None,
    ) -> dict[str, Any]:
        lifecycle = self.application.skill_lifecycle.list(limit=2000)
        counts = {
            value.value: sum(item.phase is value for item in lifecycle)
            for value in SkillLifecyclePhase
        }
        selected = [item for item in lifecycle if phase is None or item.phase is phase]
        page = selected[offset : offset + limit]
        runtime = {
            item.runtime_skill_id: item
            for item in self.application.store.list_runtime_skills(limit=2000)
        }
        learned = {
            item.skill_id: item
            for item in self.application.store.list_skills(limit=2000)
        }
        verified_tasks = self.application.store.list_tasks(
            statuses=(TaskStatus.VERIFIED_SUCCESS,),
            limit=2000,
        )
        items = [
            self._skill_detail(
                item,
                runtime.get(item.skill_id),
                learned.get(item.skill_id),
                verified_tasks,
            )
            for item in page
        ]
        next_offset = offset + len(items)
        return {
            "schema_version": "eck-workspace-skills.v1",
            "source_authority": "skill-lifecycle.v1",
            "items": items,
            "counts": {
                **counts,
                "total": len(lifecycle),
                "learning": counts[SkillLifecyclePhase.CANDIDATE.value]
                + counts[SkillLifecyclePhase.TESTING.value],
                "available": sum(item.active for item in lifecycle),
            },
            "page": {
                "limit": limit,
                "offset": offset,
                "total": len(selected),
                "next_offset": next_offset if next_offset < len(selected) else None,
            },
            "matching_policy": (
                "Only active executable runtime skills are eligible for automatic tool "
                "execution; experience skills remain verified reusable procedures."
            ),
        }

    def _current_activity(
        self,
        tasks: list[Any],
        missions: list[MissionRecord],
        executions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        running_task = next(
            (item for item in tasks if item.status is TaskStatus.RUNNING),
            tasks[0] if tasks else None,
        )
        running_project: tuple[MissionRecord, dict[str, Any], dict[str, Any]] | None = None
        for mission in missions:
            execution = executions.get(mission.mission_id, {})
            steps = list(execution.get("steps", []))
            step = next(
                (item for item in steps if item.get("status") == "running"),
                next(
                    (item for item in steps if item.get("status") == "pending"),
                    None,
                ),
            )
            if step is not None:
                running_project = (mission, execution, step)
                break

        if running_project is not None:
            mission, execution, step = running_project
            cycles = list(execution.get("cycles", []))
            latest = cycles[0] if cycles else {}
            state = "blocked" if mission.status is MissionStatus.BLOCKED else (
                "running" if step.get("status") == "running" else "queued"
            )
            return {
                "kind": "project",
                "state": state,
                "title": mission.title,
                "detail": step.get("objective") or mission.progress.get("current_step", ""),
                "project_id": mission.mission_id,
                "current_step": step.get("step_key"),
                "progress_percent": self._progress_percent(mission, execution),
                "waiting_on": self._waiting_reason(mission, steps=list(execution.get("steps", []))),
                "summary": {
                    "goal": mission.objective,
                    "plan": latest.get("reason_summary") or step.get("objective", ""),
                    "action": latest.get("action", {}),
                    "observation": latest.get("observation", {}),
                    "correction": latest.get("correction", ""),
                    "verification": latest.get("status", ""),
                    "conclusion": mission.result_summary,
                },
            }

        if running_task is not None:
            state = {
                TaskStatus.RUNNING: "running",
                TaskStatus.WAITING_APPROVAL: "waiting",
                TaskStatus.QUEUED: "queued",
            }.get(running_task.status, "idle")
            return {
                "kind": "task",
                "state": state,
                "title": running_task.goal,
                "detail": running_task.action.capability,
                "task_id": running_task.task_id,
                "current_step": running_task.action.capability,
                "progress_percent": None,
                "waiting_on": (
                    "human_approval"
                    if running_task.status is TaskStatus.WAITING_APPROVAL
                    else ""
                ),
                "summary": {
                    "goal": running_task.goal,
                    "plan": running_task.action.capability,
                    "action": {
                        "capability": running_task.action.capability,
                        "attempts": running_task.attempts,
                    },
                    "observation": (
                        running_task.result.output if running_task.result else {}
                    ),
                    "correction": running_task.last_error or "",
                    "verification": (
                        running_task.verification.status.value
                        if running_task.verification
                        else "pending"
                    ),
                    "conclusion": "",
                },
            }

        return {
            "kind": "idle",
            "state": "idle",
            "title": "等待新的工作或下一輪自主學習",
            "detail": "目前沒有執行中的原子任務或專案步驟。",
            "current_step": None,
            "progress_percent": None,
            "waiting_on": "",
            "summary": {
                "goal": "維持可驗證學習與任務準備",
                "plan": "等待排程器或使用者提供下一個可驗證目標",
                "action": {},
                "observation": {},
                "correction": "",
                "verification": "idle",
                "conclusion": "沒有虛構進度。",
            },
        }

    def _project_summary(
        self,
        mission: MissionRecord,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if execution is None and mission.status in self._active_mission_statuses:
            execution = self._execution_item(mission.mission_id)
        steps = list(execution.get("steps", [])) if execution else []
        running = next(
            (item for item in steps if item.get("status") == "running"),
            next((item for item in steps if item.get("status") == "pending"), None),
        )
        return {
            "project_id": mission.mission_id,
            "title": mission.title,
            "objective": mission.objective,
            "status": mission.status.value,
            "priority": mission.priority,
            "source": mission.source,
            "updated_at": mission.updated_at.isoformat(),
            "created_at": mission.created_at.isoformat(),
            "progress_percent": self._progress_percent(mission, execution),
            "current_step": (
                running.get("objective")
                if running
                else mission.progress.get("current_step", "")
            ),
            "step_counts": self._step_counts(steps),
            "waiting_on": self._waiting_reason(mission, steps=steps),
            "result_summary": mission.result_summary,
            "review_feedback": mission.review_feedback,
            "revision": int(mission.progress.get("human_revision_round", 0)),
            "artifacts": self._artifact_links(mission),
        }

    def _execution_item(self, mission_id: str) -> dict[str, Any]:
        try:
            status = self.application.mission_executor.status(mission_id)
        except KeyError:
            return {}
        items = list(status.get("items", []))
        if not items:
            return {}
        item = dict(items[0])
        for key in ("mission", "steps", "cycles"):
            value = item.get(key)
            dumper = getattr(value, "model_dump", None)
            if key == "mission" and callable(dumper):
                item[key] = dumper(mode="json")
            elif isinstance(value, list):
                item[key] = [
                    self._model_dump(entry)
                    for entry in value
                ]
        return item

    @staticmethod
    def _model_dump(value: Any) -> Any:
        dumper = getattr(value, "model_dump", None)
        return dumper(mode="json") if callable(dumper) else value

    def _artifact_links(self, mission: MissionRecord) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        try:
            self.application.mission_executor.preview_path(mission.mission_id)
        except (KeyError, ValueError):
            pass
        else:
            artifacts.append(
                {
                    "kind": "preview",
                    "label": "開啟成果預覽",
                    "url": f"/v1/missions/{mission.mission_id}/preview/",
                }
            )
        try:
            package = self.application.mission_executor.package_path(mission.mission_id)
        except (KeyError, ValueError):
            pass
        else:
            artifacts.append(
                {
                    "kind": "download",
                    "label": "下載成果套件",
                    "url": f"/v1/missions/{mission.mission_id}/download",
                    "filename": package.name,
                    "bytes": package.stat().st_size,
                }
            )
        return artifacts

    def _skill_detail(
        self,
        item: SkillLifecycleItem,
        runtime: Any,
        learned: Any,
        tasks: list[Any],
    ) -> dict[str, Any]:
        if item.source_kind is SkillSourceKind.RUNTIME:
            operations = list(runtime.manifest.operations) if runtime else []
            permissions = list(runtime.manifest.permissions) if runtime else []
            description = runtime.manifest.description if runtime else ""
            test_result = runtime.test_report if runtime else {}
            version = runtime.manifest.version if runtime else item.version
            source_detail: Any = {
                "kind": item.source,
                "improvements": list(runtime.improvements) if runtime else [],
            }
            actual_usages = self.application.store.list_task_skill_usages(
                runtime_skill_id=item.skill_id,
                limit=500,
            )
            used = []
            seen_tasks: set[str] = set()
            for usage in actual_usages:
                task_id = str(usage["task_id"])
                if task_id in seen_tasks:
                    continue
                try:
                    used.append(self.application.store.get_task(task_id))
                    seen_tasks.add(task_id)
                except KeyError:
                    continue
            relationship_kind = "executed"
        else:
            actual_usages = []
            operations = list(learned.procedure.keys()) if learned else []
            permissions = []
            description = (
                str(learned.procedure.get("description", "")) if learned else ""
            )
            test_result = learned.verification_basis if learned else {}
            version = item.version
            source_detail = {
                "kind": item.source,
                "verification_basis": learned.verification_basis if learned else {},
            }
            used = [task for task in tasks if task.action.capability == item.capability]
            relationship_kind = "verification_basis"
        completed = [
            {
                "task_id": task.task_id,
                "goal": task.goal,
                "completed_at": task.updated_at.isoformat(),
            }
            for task in used[:5]
        ]
        return {
            **item.model_dump(mode="json"),
            "description": description,
            "version": version,
            "scope": {
                "operations": operations,
                "permissions": permissions,
                "capability": item.capability,
            },
            "source_detail": source_detail,
            "test_result": test_result,
            "completed_task_count": len(used),
            "completed_tasks": completed,
            "actual_usage_records": actual_usages,
            "relationship_kind": relationship_kind,
        }

    @staticmethod
    def _progress_percent(
        mission: MissionRecord,
        execution: dict[str, Any] | None,
    ) -> float:
        explicit = mission.progress.get("completion_percent")
        if isinstance(explicit, int | float):
            return round(max(0.0, min(float(explicit), 100.0)), 1)
        steps = list(execution.get("steps", [])) if execution else []
        if not steps:
            return 100.0 if mission.status is MissionStatus.APPROVED else 0.0
        succeeded = sum(item.get("status") in {"succeeded", "skipped"} for item in steps)
        return round((succeeded / len(steps)) * 100, 1)

    @staticmethod
    def _step_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
        counts = {status.value: 0 for status in MissionStepStatus}
        for item in steps:
            status = str(item.get("status", ""))
            if status in counts:
                counts[status] += 1
        counts["total"] = len(steps)
        return counts

    @staticmethod
    def _waiting_reason(
        mission: MissionRecord,
        *,
        steps: list[dict[str, Any]],
    ) -> str:
        if mission.status is MissionStatus.AWAITING_REVIEW:
            return "human_review"
        if mission.status is MissionStatus.BLOCKED:
            blocked = next(
                (item for item in steps if item.get("status") == "blocked"),
                None,
            )
            return str(blocked.get("last_error", "blocked")) if blocked else "blocked"
        return ""
