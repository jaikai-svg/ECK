from __future__ import annotations

import shutil
from typing import Any

from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.models import DevelopmentProjectRequest
from eck.services.project_lab_components.base import ProjectLabMixinBase


class ProjectLabLifecycleMixin(ProjectLabMixinBase):
    async def status(self) -> dict[str, Any]:
        await self._audit_verified_projects()
        projects = self.list_projects()
        github = self.github_status()
        latest_state = self._read_json(self.state_path) if self.state_path.is_file() else None
        return {
            "enabled": self.settings.autonomous_project_lab_enabled,
            "project_count": len(projects),
            "verified_count": sum(item.get("status") == "verified" for item in projects),
            "published_count": sum(item.get("status") == "published" for item in projects),
            "failed_count": sum(
                item.get("status") in {"failed", "quality_rejected"} for item in projects
            ),
            "latest": projects[0] if projects else None,
            "last_cycle": latest_state,
            "github": github,
            "claim_policy": (
                "A generated directory is not a learned project until deterministic quality "
                "checks and isolated tests pass. "
                "A verified project is not published unless GitHub authentication and disclosure "
                "checks both pass."
            ),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        projects = []
        for path in self.root.glob("project_*/manifest.json"):
            try:
                value = self._read_json(path)
            except (OSError, ValueError):
                continue
            projects.append(value)
        return sorted(projects, key=lambda item: str(item.get("created_at", "")), reverse=True)

    def get_project(self, project_id: str) -> dict[str, Any]:
        manifest_path = self._project_dir(project_id) / "manifest.json"
        if not manifest_path.is_file():
            raise KeyError(f"Unknown autonomous project: {project_id}")
        return self._read_json(manifest_path)

    async def run_if_needed(self, *, force: bool = False) -> dict[str, Any]:
        if not self.settings.autonomous_project_lab_enabled:
            return await self._record_cycle("disabled", "Autonomous project lab is disabled.")
        if not force and not self._cycle_due():
            return await self._record_cycle(
                "waiting_interval", "Project incubation interval not due."
            )
        if not await self.worker.image_available():
            return await self._record_cycle(
                "waiting_worker", "The isolated Docker worker image is not available."
            )
        health = await self.coder_brain.health()
        if not health.available:
            return await self._record_cycle("waiting_coder", health.detail)
        research = self._eligible_research()
        if len(research) < self.settings.autonomous_project_min_research_runs:
            return await self._record_cycle(
                "waiting_research",
                (
                    f"Need {self.settings.autonomous_project_min_research_runs} unused, conclusive "
                    f"research runs; found {len(research)}."
                ),
            )
        selected = research[:1]
        lead = selected[0]
        topic = str(lead.get("topic", "verified research"))
        objective = (
            "Build a small, reproducible, local Python project that turns the supplied verified "
            f"research into an executable experiment. Lead topic: {topic}"
        )
        request = DevelopmentProjectRequest(
            objective=objective,
            research_run_ids=tuple(str(item["run_id"]) for item in selected),
            visibility=self.settings.github_default_visibility,
            publish_when_verified=self.settings.github_auto_publish_verified_projects,
        )
        project = await self.create(request)
        return await self._record_cycle(
            str(project["status"]),
            f"Autonomous project {project['project_id']} finished as {project['status']}.",
            project_id=str(project["project_id"]),
        )

    async def create(self, request: DevelopmentProjectRequest) -> dict[str, Any]:
        research = [self.store.get_research_run(run_id) for run_id in request.research_run_ids]
        if research and any(
            item.get("conclusion_status") not in {"supported", "partially_supported"}
            for item in research
        ):
            raise ValueError("Project evidence must come from conclusive research runs.")
        if not await self.worker.image_available():
            raise RuntimeError("The isolated Docker worker image is not available.")
        project_id = new_id("project")
        project_dir = self._project_dir(project_id)
        source_dir = project_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=False)
        try:
            feedback: str | None = None
            draft: dict[str, Any] = {}
            validation: dict[str, Any] = {
                "success": False,
                "detail": "No project draft was attempted.",
            }
            attempt_reports: list[dict[str, Any]] = []
            previous_draft: dict[str, Any] | None = None
            for attempt in range(1, self.settings.autonomous_project_draft_attempts + 1):
                try:
                    candidate_draft = await self._draft(
                        request,
                        research,
                        feedback=feedback,
                        previous_draft=previous_draft if feedback else None,
                    )
                    previous_draft = candidate_draft
                    files = self._validate_files(candidate_draft.get("files", []))
                    self._clear_source_dir(project_dir, source_dir)
                    self._write_project_files(source_dir, files)
                    self._ensure_disclosure(source_dir, request.objective, research)
                    self._scan_secrets(source_dir)
                except ValueError as exc:
                    feedback = f"Draft contract failed: {exc}"
                    attempt_reports.append(
                        {"attempt": attempt, "success": False, "detail": feedback}
                    )
                    if attempt >= self.settings.autonomous_project_draft_attempts:
                        break
                    continue
                draft = candidate_draft
                previous_draft = draft
                quality = self._static_quality_gate(source_dir, objective=request.objective)
                if quality["success"]:
                    validation = await self._validate_in_docker(source_dir)
                    validation["quality"] = quality
                else:
                    validation = {
                        "success": False,
                        "returncode": None,
                        "detail": "Static project quality contract failed.",
                        "output_tail": "; ".join(quality["issues"]),
                        "isolated": False,
                        "network": "none",
                        "quality": quality,
                    }
                detail = str(
                    validation.get("output_tail", validation.get("detail", ""))
                )
                attempt_reports.append(
                    {
                        "attempt": attempt,
                        "success": bool(validation["success"]),
                        "detail": detail[-2000:],
                    }
                )
                if validation["success"]:
                    break
                feedback = (
                    "Isolated pytest failed. Return a complete corrected project. "
                    f"Failure output: {detail[-3000:]}"
                )
            if not validation["success"]:
                split_dir = project_dir / "split-candidate"
                split_dir.mkdir(exist_ok=False)
                try:
                    split_draft = await self._draft_split(
                        request,
                        research,
                        feedback=feedback or "The multi-file project contract was not satisfied.",
                        previous_draft=previous_draft,
                    )
                    split_files = self._validate_files(split_draft.get("files", []))
                    self._write_project_files(split_dir, split_files)
                    self._ensure_disclosure(split_dir, request.objective, research)
                    self._scan_secrets(split_dir)
                    split_quality = self._static_quality_gate(
                        split_dir, objective=request.objective
                    )
                    if split_quality["success"]:
                        split_validation = await self._validate_in_docker(split_dir)
                        split_validation["quality"] = split_quality
                    else:
                        split_validation = {
                            "success": False,
                            "returncode": None,
                            "detail": "Split-file static quality contract failed.",
                            "output_tail": "; ".join(split_quality["issues"]),
                            "isolated": False,
                            "network": "none",
                            "quality": split_quality,
                        }
                    split_detail = str(
                        split_validation.get(
                            "output_tail", split_validation.get("detail", "")
                        )
                    )
                    attempt_reports.append(
                        {
                            "attempt": "split-file",
                            "success": bool(split_validation["success"]),
                            "detail": split_detail[-2000:],
                        }
                    )
                    if split_quality["success"] and not split_validation["success"]:
                        source_content = next(
                            item["content"]
                            for item in split_files
                            if item["path"] == "experiment.py"
                        )
                        repaired_tests = await self._repair_split_tests(
                            request,
                            source_content,
                            failure=split_detail,
                        )
                        for item in split_files:
                            if item["path"] == "tests/test_experiment.py":
                                item["content"] = repaired_tests
                        (split_dir / "tests" / "test_experiment.py").write_text(
                            repaired_tests,
                            encoding="utf-8",
                        )
                        repaired_quality = self._static_quality_gate(
                            split_dir, objective=request.objective
                        )
                        if repaired_quality["success"]:
                            repaired_validation = await self._validate_in_docker(split_dir)
                            repaired_validation["quality"] = repaired_quality
                        else:
                            repaired_validation = {
                                "success": False,
                                "returncode": None,
                                "detail": "Split test repair quality contract failed.",
                                "output_tail": "; ".join(repaired_quality["issues"]),
                                "isolated": False,
                                "network": "none",
                                "quality": repaired_quality,
                            }
                        repaired_detail = str(
                            repaired_validation.get(
                                "output_tail", repaired_validation.get("detail", "")
                            )
                        )
                        attempt_reports.append(
                            {
                                "attempt": "split-test-repair",
                                "success": bool(repaired_validation["success"]),
                                "detail": repaired_detail[-2000:],
                            }
                        )
                        split_validation = repaired_validation
                    if split_validation["success"] or not draft:
                        self._clear_source_dir(project_dir, source_dir)
                        self._write_project_files(source_dir, split_files)
                        self._ensure_disclosure(source_dir, request.objective, research)
                        draft = split_draft
                        validation = split_validation
                except ValueError as exc:
                    attempt_reports.append(
                        {
                            "attempt": "split-file",
                            "success": False,
                            "detail": f"Split-file contract failed: {exc}",
                        }
                    )
                    if not draft:
                        raise
                finally:
                    shutil.rmtree(split_dir, ignore_errors=True)
            validation["draft_attempts"] = attempt_reports
            name = request.name or self._safe_project_name(
                str(draft.get("name", "")), project_id
            )
            source_hash = self._source_hash(source_dir)
            status = "verified" if validation["success"] else "failed"
            manifest: dict[str, Any] = {
                "schema_version": "eck-autonomous-project.v1",
                "project_id": project_id,
                "name": name,
                "objective": request.objective,
                "summary": str(draft.get("summary", ""))[:4000],
                "status": status,
                "model": str(draft.get("model", "")),
                "source_dir": str(source_dir),
                "source_sha256": source_hash,
                "research_run_ids": [str(item["run_id"]) for item in research],
                "source_urls": sorted(
                    {
                        str(source["canonical_url"])
                        for item in research
                        for source in item.get("sources", [])
                        if source.get("canonical_url")
                    }
                ),
                "validation": validation,
                "visibility": request.visibility or self.settings.github_default_visibility,
                "github": {"published": False},
                "created_at": utc_now().isoformat(),
                "updated_at": utc_now().isoformat(),
            }
            self._write_manifest(project_dir, manifest)
            await self.events.publish(
                "AutonomousProjectVerified" if validation["success"] else "AutonomousProjectFailed",
                project_id,
                {
                    "name": name,
                    "source_sha256": source_hash,
                    "research_run_ids": manifest["research_run_ids"],
                },
                correlation_id=project_id,
            )
            if validation["success"] and request.publish_when_verified:
                manifest = await self.publish(project_id)
            return manifest
        except Exception:
            if not (project_dir / "manifest.json").is_file():
                shutil.rmtree(project_dir, ignore_errors=True)
            raise


