from __future__ import annotations

from pathlib import Path
from typing import Any

from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.domain.models import MissionReactCycleRecord, MissionRecord, MissionStepRecord
from eck.experimental.p6.deliberation import DeliberationResult
from eck.experimental.p6.executor_base import MissionExecutorMixinBase, StepOutcome


class MissionExecutionMixin(MissionExecutorMixinBase):
    async def run_next(self) -> MissionStepRecord | None:
        if not self.settings.durable_mission_executor_enabled:
            return None
        step = self.store.claim_next_mission_step()
        if step is None:
            return None
        mission = self.store.get_mission(step.mission_id)
        deliberation = await self._reason_before_action(mission, step)
        action = {
            "tool": step.action_kind,
            "step_key": step.step_key,
            "attempt": step.attempts,
            "input_keys": sorted(step.inputs),
            "agent_role": deliberation.role,
            "agent_role_label": deliberation.role_label,
            "deliberation_rounds": deliberation.rounds,
            "ready_for_action": deliberation.ready_for_action,
            "deliberation_model": deliberation.model,
        }
        cycle = self.store.create_mission_react_cycle(
            step,
            reason_summary=deliberation.summary,
            action=action,
        )
        await self.events.publish(
            "MissionReactActionStarted",
            step.step_id,
            {
                "mission_id": step.mission_id,
                "step_key": step.step_key,
                "attempt": step.attempts,
                "reason_summary": deliberation.summary,
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

    def has_urgent_runnable_work(self) -> bool:
        return self._first_urgent_runnable_step() is not None

    def has_urgent_low_resource_work(self) -> bool:
        step = self._first_urgent_runnable_step()
        return step is not None and step.action_kind in self._low_resource_actions

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
            "executor": self._executor_version,
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
            "agent_pipeline": {
                "execution": "sequential",
                "concurrency": 1,
                "maximum_deliberation_rounds": self.deliberation.max_rounds,
                "dynamic_stop": True,
                "private_chain_of_thought_exposed": False,
                "roles": self.deliberation.pipeline,
            },
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
    ) -> DeliberationResult:
        deterministic_actions = {
            "workspace.prepare",
            "software.validate",
            "learning.distill",
            "artifact.package",
            "github.publish",
            "mission.submit",
        }
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
        return await self.deliberation.deliberate(
            mission,
            step,
            observations=observations,
            use_model=step.action_kind not in deterministic_actions,
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
