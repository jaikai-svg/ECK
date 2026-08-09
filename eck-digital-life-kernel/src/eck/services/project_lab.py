from __future__ import annotations

import ast
import asyncio
import builtins
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.models import DevelopmentProjectRequest
from eck.events.bus import EventBus
from eck.runtime.worker import DockerSkillWorker
from eck.storage.sqlite import SQLiteStore


class AutonomousProjectLabService:
    _project_id_pattern = re.compile(r"project_[a-f0-9]{32}")
    _safe_name_pattern = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
    _secret_patterns = (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]{8,}"),
    )

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        coder_brain: BrainProvider,
        worker: DockerSkillWorker,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.coder_brain = coder_brain
        self.worker = worker
        self.root = settings.project_lab_dir
        self.state_path = settings.project_lab_state_path
        self._github_cache: dict[str, Any] | None = None
        self._github_checked_at = 0.0
        self.root.mkdir(parents=True, exist_ok=True)

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

    async def publish(self, project_id: str) -> dict[str, Any]:
        manifest = self.get_project(project_id)
        if manifest.get("status") not in {"verified", "publish_failed"}:
            raise RuntimeError("Only verified autonomous projects can be published.")
        if not self.settings.github_publish_enabled:
            return self._publish_deferred(manifest, "GitHub publishing is disabled.")
        github = self.github_status()
        if not github["ready"]:
            return self._publish_deferred(manifest, str(github["detail"]))
        source_dir = Path(str(manifest["source_dir"]))
        self._scan_secrets(source_dir)
        self._initialize_git(source_dir)
        account = self.settings.github_account or str(github["account"])
        repository = f"{account}/{manifest['name']}"
        visibility = str(manifest.get("visibility", "private"))
        executable = str(github["executable"])
        token = self._github_token(executable, account)
        if token is None:
            return self._publish_deferred(
                manifest,
                f"GitHub credentials for the dedicated account {account!r} are unavailable.",
            )
        result = await self._run_process(
            [
                executable,
                "repo",
                "create",
                repository,
                f"--{visibility}",
                "--source",
                str(source_dir),
                "--remote",
                "origin",
                "--push",
            ],
            cwd=source_dir,
            timeout=180,
            env=self._github_environment(token),
        )
        manifest["github"] = {
            "published": result["returncode"] == 0,
            "repository": repository,
            "url": f"https://github.com/{repository}",
            "detail": result["output_tail"],
        }
        manifest["status"] = "published" if result["returncode"] == 0 else "publish_failed"
        manifest["updated_at"] = utc_now().isoformat()
        self._write_manifest(self._project_dir(project_id), manifest)
        await self.events.publish(
            "AutonomousProjectPublished" if result["returncode"] == 0 else "ProjectPublishFailed",
            project_id,
            {"repository": repository, "returncode": result["returncode"]},
            correlation_id=project_id,
        )
        return manifest

    async def publish_directory(
        self,
        *,
        name: str,
        source_dir: Path,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        """Publish an already verified project without coupling it to project-lab manifests."""
        if not self.settings.github_publish_enabled:
            return {
                "published": False,
                "deferred": True,
                "detail": "GitHub publishing is disabled.",
            }
        if not source_dir.is_dir():
            raise ValueError("Verified project source directory is missing.")
        safe_name = self._safe_project_name(name, new_id("project"))
        selected_visibility = visibility or self.settings.github_default_visibility
        if selected_visibility not in {"private", "public"}:
            raise ValueError("GitHub visibility must be private or public.")
        github = self.github_status()
        if not github["ready"]:
            return {
                "published": False,
                "deferred": True,
                "detail": str(github["detail"]),
            }
        self._scan_secrets(source_dir)
        self._initialize_git(source_dir)
        account = self.settings.github_account or str(github["account"])
        repository = f"{account}/{safe_name}"
        executable = str(github["executable"])
        token = self._github_token(executable, account)
        if token is None:
            return {
                "published": False,
                "deferred": True,
                "repository": repository,
                "detail": f"GitHub credentials for {account!r} are unavailable.",
            }
        result = await self._run_process(
            [
                executable,
                "repo",
                "create",
                repository,
                f"--{selected_visibility}",
                "--source",
                str(source_dir),
                "--remote",
                "origin",
                "--push",
            ],
            cwd=source_dir,
            timeout=180,
            env=self._github_environment(token),
        )
        published = result["returncode"] == 0
        return {
            "published": published,
            "deferred": False,
            "repository": repository,
            "url": f"https://github.com/{repository}",
            "detail": result["output_tail"],
        }

    async def validate_python_directory(
        self,
        source_dir: Path,
        *,
        objective: str,
    ) -> dict[str, Any]:
        if not source_dir.is_dir():
            return {"success": False, "detail": "Python project source directory is missing."}
        self._scan_secrets(source_dir)
        quality = self._static_quality_gate(source_dir, objective=objective)
        if not quality["success"]:
            return {
                "success": False,
                "detail": "Static Python quality contract failed.",
                "output_tail": "; ".join(quality["issues"]),
                "quality": quality,
                "isolated": False,
            }
        validation = await self._validate_in_docker(source_dir)
        validation["quality"] = quality
        return validation

    def github_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._github_cache is not None
            and now - self._github_checked_at < 30
        ):
            return self._github_cache
        executable = self._gh_executable()
        if executable is None:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": False,
                "detail": "GitHub CLI is not installed.",
            })
        expected = self.settings.github_account
        token: str | None = None
        environment: dict[str, str] | None = None
        if expected:
            token = self._github_token(executable, expected)
            if token is None:
                return self._cache_github(now, {
                    "ready": False,
                    "authenticated": False,
                    "account": expected,
                    "executable": executable,
                    "detail": (
                        f"GitHub CLI has no stored OAuth credential for the dedicated "
                        f"ECK account {expected!r}."
                    ),
                })
            environment = self._github_environment(token)
        try:
            account_result = subprocess.run(
                [executable, "api", "user", "--jq", ".login"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": True,
                "executable": executable,
                "detail": f"GitHub account lookup failed: {type(exc).__name__}: {exc}",
            })
        if account_result.returncode != 0:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": False,
                "account": expected,
                "executable": executable,
                "detail": "GitHub CLI is installed but the requested account is unavailable.",
            })
        account = account_result.stdout.strip()
        if not expected:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": True,
                "account": account,
                "executable": executable,
                "detail": (
                    "GitHub CLI is authenticated, but the dedicated ECK account is not "
                    "configured. Set ECK_GITHUB_ACCOUNT before autonomous publication."
                ),
            })
        if expected and account.casefold() != expected.casefold():
            return self._cache_github(now, {
                "ready": False,
                "authenticated": True,
                "account": account,
                "executable": executable,
                "detail": f"Authenticated GitHub account is {account!r}, expected {expected!r}.",
            })
        return self._cache_github(now, {
            "ready": bool(account),
            "authenticated": True,
            "account": account,
            "executable": executable,
            "detail": (
                "GitHub publisher is ready."
                if account
                else "GitHub account was not resolved."
            ),
        })

    def _cache_github(self, checked_at: float, value: dict[str, Any]) -> dict[str, Any]:
        self._github_checked_at = checked_at
        self._github_cache = value
        return value

    @staticmethod
    def _github_token(executable: str, account: str) -> str | None:
        try:
            result = subprocess.run(
                [
                    executable,
                    "auth",
                    "token",
                    "--hostname",
                    "github.com",
                    "--user",
                    account,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        token = result.stdout.strip()
        return token if result.returncode == 0 and token else None

    @staticmethod
    def _github_environment(token: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("GITHUB_TOKEN", None)
        environment["GH_TOKEN"] = token
        environment["GH_HOST"] = "github.com"
        return environment

    async def _draft(
        self,
        request: DevelopmentProjectRequest,
        research: list[dict[str, Any]],
        *,
        feedback: str | None = None,
        previous_draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence = [self._compact_research_evidence(item) for item in research]
        response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are ECK's autonomous project engineer. Build one small Python 3.11 "
                        "project from verified research. Use only the standard library. Include "
                        "at least one executable .py file outside tests and at least one test at "
                        "the exact path pattern tests/test_*.py. Tests must run from the project "
                        "root with python -m pytest -q. Include a useful README. Do not claim "
                        "results that tests cannot prove. Do not include credentials, network "
                        "calls, "
                        "subprocess, "
                        "shell commands, package installation, telemetry, or hidden downloads. "
                        "Do not use random, current time, placeholders, mocked success, or "
                        "simulated "
                        "measurements. Tests must assert exact behavior or numeric invariants, not "
                        "only types or non-null values. Do not include requirements.txt because "
                        "the project must use only the standard library. Tests must execute the "
                        "real implementation and may not mock or patch it. "
                        "Use at least two distinctive words from the objective in meaningful "
                        "function, class, or module names so the implementation is audibly tied "
                        "to the requested research topic. "
                        "When previous-attempt feedback is present, repair those files minimally, "
                        "preserve Python newlines and indentation, and return every complete file. "
                        "Return complete files, not prose patches."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "research": evidence,
                            "previous_attempt_feedback": feedback,
                            "previous_attempt": self._compact_previous_draft(previous_draft),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 4096},
            format_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
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
                    },
                },
                "required": ["name", "summary", "files"],
            },
        )
        payload = self._json_object(response.content)
        payload["model"] = response.model
        return payload

    async def _draft_split(
        self,
        request: DevelopmentProjectRequest,
        research: list[dict[str, Any]],
        *,
        feedback: str,
        previous_draft: dict[str, Any] | None,
    ) -> dict[str, Any]:
        context = {
            "objective": request.objective,
            "research": [self._compact_research_evidence(item) for item in research],
            "failure": feedback,
            "previous_attempt": self._compact_previous_draft(previous_draft),
        }
        source_response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Write the complete raw contents of experiment.py for one small, "
                        "deterministic Python 3.11 experiment. Return Python only, without a "
                        "Markdown fence or explanation. Use only the standard library. Do not use "
                        "random, time, network, subprocess, external files, placeholders, "
                        "simulated "
                        "success, input(), or package installation. Implement measurable behavior "
                        "with stable inputs and outputs that tests can verify exactly. Use at "
                        "least "
                        "two distinctive objective words in meaningful function or class names."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        source = self._plain_code(source_response.content)
        test_response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Write the complete raw contents of tests/test_experiment.py for the "
                        "provided experiment.py. Return Python only, without a Markdown fence or "
                        "explanation. Import from experiment. Include at least two behavioral "
                        "assertions with exact expected values or numeric invariants. Do not use "
                        "only type, truthiness, or non-null assertions. Use no network or files."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "experiment.py": source[:8000],
                            "previous_failure": feedback,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        tests = self._plain_code(test_response.content)
        suffix = hashlib.sha256(request.objective.encode("utf-8")).hexdigest()[:10]
        return {
            "name": f"eck-experiment-{suffix}",
            "summary": (
                "A bounded split-file repair generated from verified research and validated "
                "against deterministic behavior."
            ),
            "files": [
                {"path": "experiment.py", "content": source},
                {"path": "tests/test_experiment.py", "content": tests},
            ],
            "model": source_response.model,
        }

    async def _repair_split_tests(
        self,
        request: DevelopmentProjectRequest,
        source: str,
        *,
        failure: str,
    ) -> str:
        tree = ast.parse(source, filename="experiment.py")
        functions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Repair only tests/test_experiment.py. Return complete raw Python without "
                        "a Markdown fence or explanation. Import only real names listed in "
                        "available_functions from experiment. Include at least two deterministic "
                        "behavioral assertions using exact expected values or numeric invariants. "
                        "Do not use mocks, files, network, external packages, type-only checks, "
                        "truthiness-only checks, or names absent from experiment.py."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "available_functions": functions,
                            "experiment.py": source[:8000],
                            "pytest_failure": failure[-3000:],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        return self._plain_code(response.content)

    @staticmethod
    def _plain_code(content: str) -> str:
        cleaned = content.strip()
        fenced = re.search(r"```(?:python)?\s*(.*?)```", cleaned, flags=re.I | re.S)
        if fenced:
            cleaned = fenced.group(1).strip()
        return cleaned + "\n" if cleaned else ""

    @staticmethod
    def _compact_previous_draft(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        remaining = 8000
        files: list[dict[str, str]] = []
        for item in value.get("files", []):
            if not isinstance(item, dict) or remaining <= 0:
                continue
            path = str(item.get("path", ""))[:300]
            content = str(item.get("content", ""))[: min(remaining, 4000)]
            remaining -= len(content)
            files.append({"path": path, "content": content})
        return {
            "name": str(value.get("name", ""))[:100],
            "summary": str(value.get("summary", ""))[:500],
            "files": files,
        }

    @staticmethod
    def _compact_research_evidence(item: dict[str, Any]) -> dict[str, Any]:
        claims = [
            {
                "claim": str(claim.get("claim", ""))[:400],
                "status": str(claim.get("status", ""))[:40],
                "confidence": claim.get("confidence"),
            }
            for claim in item.get("claims", [])[:3]
            if isinstance(claim, dict)
        ]
        sources = [
            {
                "canonical_url": str(source.get("canonical_url", ""))[:300],
                "title": str(source.get("title", ""))[:180],
                "source_domain": str(source.get("source_domain", ""))[:120],
                "published_at": str(source.get("published_at", ""))[:40],
            }
            for source in item.get("sources", [])[:3]
            if isinstance(source, dict)
        ]
        return {
            "run_id": str(item.get("run_id", "")),
            "topic": str(item.get("topic", ""))[:300],
            "conclusion": str(item.get("conclusion", ""))[:600],
            "claims": claims,
            "sources": sources,
        }

    @staticmethod
    def _clear_source_dir(project_dir: Path, source_dir: Path) -> None:
        project_root = project_dir.resolve()
        source_root = source_dir.resolve()
        if source_root.parent != project_root or source_root.name != "source":
            raise ValueError("Autonomous project source directory escaped its project root.")
        for path in source_root.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def _validate_files(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            raise ValueError("Project model returned no files.")
        files: list[dict[str, str]] = []
        total_bytes = 0
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Project file entry must be an object.")
            path = self._safe_relative_path(str(item.get("path", "")))
            content = str(item.get("content", ""))
            if path in seen:
                raise ValueError(f"Duplicate autonomous project file: {path}")
            if not content:
                if path.casefold() == "requirements.txt":
                    continue
                raise ValueError(f"Empty autonomous project file: {path}")
            if not path.endswith((".md", ".py", ".toml", ".txt", ".json", ".yaml", ".yml")):
                raise ValueError(f"Unsupported autonomous project file type: {path}")
            seen.add(path)
            total_bytes += len(content.encode("utf-8"))
            files.append({"path": path, "content": content})
        if len(files) > 30 or total_bytes > 500_000:
            raise ValueError("Autonomous project exceeds the file or byte limit.")
        if not any(item["path"].startswith("tests/test_") for item in files):
            raise ValueError("Autonomous project must include deterministic pytest tests.")
        if not any(
            item["path"].endswith(".py") and not item["path"].startswith("tests/")
            for item in files
        ):
            raise ValueError("Autonomous project must include executable Python source.")
        return files

    @staticmethod
    def _write_project_files(source_dir: Path, files: list[dict[str, str]]) -> None:
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")

    @staticmethod
    def _ensure_disclosure(
        source_dir: Path,
        objective: str,
        research: list[dict[str, Any]],
    ) -> None:
        readme = source_dir / "README.md"
        existing = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        disclosure = (
            "# AI/ECK Autonomous Project\n\n"
            "This project was autonomously drafted by ECK, tested in an isolated local worker, "
            "and published with explicit AI/ECK disclosure. Human review is still recommended.\n\n"
            f"## Objective\n\n{objective}\n\n"
            "## Research Evidence\n\n"
            f"{len(research)} verified ECK research runs informed this prototype.\n\n"
        )
        if "AI/ECK" not in existing:
            readme.write_text(disclosure + existing, encoding="utf-8")

    def _scan_secrets(self, source_dir: Path) -> None:
        for path in source_dir.rglob("*"):
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(text) for pattern in self._secret_patterns):
                raise ValueError(f"Potential secret detected in autonomous project: {path.name}")

    @staticmethod
    def _static_quality_gate(
        source_dir: Path,
        *,
        objective: str | None = None,
    ) -> dict[str, Any]:
        issues: list[str] = []
        source_functions = 0
        behavioral_assertions = 0
        weak_assertions = {"assertIsInstance", "assertIsNotNone", "assertTrue"}
        placeholder_phrases = {
            "optimization successful",
            "simulate quantization",
            "simulated measurement",
            "placeholder",
            "not implemented",
            "dummy result",
        }
        local_modules = {
            path.stem
            for path in source_dir.rglob("*.py")
            if not path.relative_to(source_dir).as_posix().startswith("tests/")
        }
        allowed_imports = set(sys.stdlib_module_names) | local_modules | {"pytest"}
        identifier_terms = {
            token
            for path in source_dir.rglob("*.py")
            for token in re.findall(
                r"[a-z][a-z0-9]{2,}",
                path.relative_to(source_dir).as_posix().casefold().replace("_", " "),
            )
        }
        for path in sorted(source_dir.rglob("*.py")):
            relative = path.relative_to(source_dir).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text, filename=relative)
            except SyntaxError as exc:
                issues.append(f"Python syntax error in {relative}: line {exc.lineno}.")
                continue
            is_test = relative.startswith("tests/test_")
            module_defined = set(dir(builtins))
            for top_level in tree.body:
                if isinstance(top_level, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    module_defined.add(top_level.name)
                elif isinstance(top_level, ast.Import):
                    module_defined.update(
                        alias.asname or alias.name.split(".", maxsplit=1)[0]
                        for alias in top_level.names
                    )
                elif isinstance(top_level, ast.ImportFrom):
                    module_defined.update(alias.asname or alias.name for alias in top_level.names)
                elif isinstance(top_level, (ast.Assign, ast.AnnAssign)):
                    module_defined.update(
                        name.id
                        for name in ast.walk(top_level)
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                    )
            for node in ast.walk(tree):
                if isinstance(
                    node,
                    (ast.Name, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    identifier = node.id if isinstance(node, ast.Name) else node.name
                    identifier_terms.update(
                        re.findall(
                            r"[a-z][a-z0-9]{2,}",
                            re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier)
                            .casefold()
                            .replace("_", " "),
                        )
                    )
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom):
                        names = (
                            {node.module.split(".", maxsplit=1)[0]} if node.module else set()
                        )
                    else:
                        names = {
                            alias.name.split(".", maxsplit=1)[0] for alias in node.names
                        }
                    external = names - allowed_imports
                    if external:
                        issues.append(
                            f"Non-standard dependency in {relative}: {', '.join(sorted(external))}."
                        )
                    if names & {"random", "secrets", "time"}:
                        issues.append(f"Non-deterministic import in {relative}.")
                    if is_test and (
                        "unittest.mock" in text
                        or names & {"mock", "unittest.mock"}
                    ):
                        issues.append("Tests may not mock or patch the implementation under test.")
                if not is_test and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    source_functions += 1
                    body = [
                        item
                        for item in node.body
                        if not (
                            isinstance(item, ast.Expr)
                            and isinstance(item.value, ast.Constant)
                            and isinstance(item.value.value, str)
                        )
                    ]
                    if len(body) == 1 and (
                        isinstance(body[0], ast.Pass)
                        or (
                            isinstance(body[0], ast.Return)
                            and isinstance(body[0].value, ast.Constant)
                        )
                    ):
                        issues.append(f"Trivial constant function {node.name} in {relative}.")
                    local_defined = {
                        argument.arg
                        for argument in (
                            list(node.args.posonlyargs)
                            + list(node.args.args)
                            + list(node.args.kwonlyargs)
                        )
                    }
                    if node.args.vararg:
                        local_defined.add(node.args.vararg.arg)
                    if node.args.kwarg:
                        local_defined.add(node.args.kwarg.arg)
                    local_defined.update(
                        name.id
                        for name in ast.walk(node)
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                    )
                    undefined = {
                        name.id
                        for name in ast.walk(node)
                        if isinstance(name, ast.Name)
                        and isinstance(name.ctx, ast.Load)
                        and name.id not in module_defined
                        and name.id not in local_defined
                    }
                    if undefined:
                        issues.append(
                            f"Undefined names in {relative}:{node.name}: "
                            f"{', '.join(sorted(undefined))}."
                        )
                if is_test and isinstance(node, ast.Assert):
                    behavioral_assertions += 1
                if (
                    is_test
                    and isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("assert")
                    and node.func.attr not in weak_assertions
                ):
                    behavioral_assertions += 1
            lowered = text.casefold()
            if any(phrase in lowered for phrase in placeholder_phrases):
                issues.append(f"Placeholder or simulated success text in {relative}.")
        if source_functions == 0:
            issues.append("No executable source function was found.")
        if behavioral_assertions < 2:
            issues.append("Tests require at least two behavioral assertions.")
        objective_keywords: set[str] = set()
        matched_keywords: set[str] = set()
        if objective:
            focus = objective.split("Lead topic:", maxsplit=1)[-1]
            stopwords = {
                "build",
                "small",
                "reproducible",
                "local",
                "python",
                "project",
                "turns",
                "supplied",
                "verified",
                "research",
                "executable",
                "experiment",
                "current",
                "evidence",
                "credibility",
                "methods",
                "measurement",
                "reproducibility",
            }
            objective_keywords = {
                token
                for token in re.findall(r"[a-z][a-z0-9]{3,}", focus.casefold())
                if token not in stopwords and not token.isdigit()
            }
            matched_keywords = objective_keywords & identifier_terms
            if len(objective_keywords) >= 2 and len(matched_keywords) < 2:
                issues.append(
                    "Implementation identifiers are not relevant to the objective; matched "
                    f"{', '.join(sorted(matched_keywords)) or 'none'}."
                )
        return {
            "success": not issues,
            "issues": sorted(set(issues)),
            "source_functions": source_functions,
            "behavioral_assertions": behavioral_assertions,
            "objective_keywords": sorted(objective_keywords),
            "matched_objective_keywords": sorted(matched_keywords),
        }

    async def _audit_verified_projects(self) -> None:
        for manifest in self.list_projects():
            status = manifest.get("status")
            validation = manifest.get("validation", {})
            if status not in {"verified", "quality_rejected"}:
                continue
            if status == "quality_rejected" and not validation.get("success"):
                continue
            source_dir = Path(str(manifest.get("source_dir", "")))
            quality = (
                self._static_quality_gate(
                    source_dir,
                    objective=str(manifest.get("objective", "")),
                )
                if source_dir.is_dir()
                else {"success": False, "issues": ["Project source directory is missing."]}
            )
            if quality["success"]:
                continue
            manifest["status"] = "quality_rejected"
            validation = manifest.setdefault("validation", {})
            validation["isolated_test_success"] = bool(validation.get("success"))
            validation["success"] = False
            validation["post_audit"] = quality
            manifest["updated_at"] = utc_now().isoformat()
            self._write_manifest(
                self._project_dir(str(manifest["project_id"])),
                manifest,
            )
            if status == "verified":
                await self.events.publish(
                    "AutonomousProjectQualityRejected",
                    str(manifest["project_id"]),
                    {"issues": quality["issues"]},
                    correlation_id=str(manifest["project_id"]),
                )
            state = self._read_json(self.state_path) if self.state_path.is_file() else {}
            if state.get("project_id") == manifest["project_id"]:
                await self._record_cycle(
                    "quality_rejected",
                    (
                        f"Autonomous project {manifest['project_id']} was rejected by the "
                        "current deterministic quality contract."
                    ),
                    project_id=str(manifest["project_id"]),
                )

    async def _validate_in_docker(self, source_dir: Path) -> dict[str, Any]:
        image_status = await self.worker.image_status()
        if not image_status.get("available"):
            return {"success": False, "detail": image_status.get("detail", "Worker unavailable.")}
        executable = str(image_status["executable"])
        command = [
            executable,
            "run",
            "--rm",
            "--read-only",
            "--network",
            "none",
            "--memory",
            f"{self.settings.skill_worker_memory_mb}m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=256m,mode=1777",
            "--mount",
            f"type=bind,source={source_dir.resolve()},target=/project,readonly",
            "--workdir",
            "/project",
            "--entrypoint",
            "python",
            self.settings.skill_worker_image,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        result = await self._run_process(
            command,
            cwd=source_dir,
            timeout=self.settings.skill_worker_timeout_seconds,
        )
        return {
            "success": result["returncode"] == 0,
            "test_command": [
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            "returncode": result["returncode"],
            "output_tail": result["output_tail"],
            "isolated": True,
            "network": "none",
        }

    def _eligible_research(self) -> list[dict[str, Any]]:
        used = {
            str(run_id)
            for project in self.list_projects()
            if project.get("status") in {"verified", "published"}
            for run_id in project.get("research_run_ids", [])
        }
        selected: list[dict[str, Any]] = []
        topics: set[str] = set()
        for item in self.store.list_research_runs(limit=200):
            if (
                item.get("status") != "completed"
                or item.get("conclusion_status") not in {"supported", "partially_supported"}
                or len(item.get("sources", [])) < 2
                or str(item.get("run_id")) in used
            ):
                continue
            topic = re.sub(
                r"[（(]\s*第\s*\d+\s*輪\s*[）)]",
                "",
                str(item.get("topic", "")),
            ).strip().casefold()
            if topic == "自主學習品質與證據覆蓋改善":
                continue
            if not topic or topic in topics:
                continue
            topics.add(topic)
            selected.append(item)
        return selected

    def _cycle_due(self) -> bool:
        projects = self.list_projects()
        now = utc_now()
        recent = [
            item
            for item in projects
            if self._parse_time(str(item.get("created_at", "1970-01-01T00:00:00+00:00")))
            >= now - timedelta(days=1)
        ]
        if (
            self.settings.autonomous_project_max_per_day > 0
            and len(recent) >= self.settings.autonomous_project_max_per_day
        ):
            return False
        if not projects:
            return True
        latest = self._parse_time(str(projects[0]["created_at"]))
        return (now - latest).total_seconds() >= self.settings.autonomous_project_interval_seconds

    async def _record_cycle(
        self,
        status: str,
        message: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        state = {
            "schema_version": "eck-autonomous-project-cycle.v1",
            "attempted_at": utc_now().isoformat(),
            "status": status,
            "message": message,
            "project_id": project_id,
        }
        self._write_json(self.state_path, state)
        return state

    def _publish_deferred(self, manifest: dict[str, Any], detail: str) -> dict[str, Any]:
        manifest["github"] = {"published": False, "deferred": True, "detail": detail}
        manifest["updated_at"] = utc_now().isoformat()
        self._write_manifest(self._project_dir(str(manifest["project_id"])), manifest)
        return manifest

    @staticmethod
    def _initialize_git(source_dir: Path) -> None:
        if (source_dir / ".git").exists():
            return
        commands = (
            ["git", "init", "-b", "main"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                "user.name=ECK Autonomous Developer",
                "-c",
                "user.email=eck-local@users.noreply.github.com",
                "commit",
                "-m",
                "Initial verified ECK project",
            ],
        )
        for command in commands:
            result = subprocess.run(
                command,
                cwd=source_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"Local project Git initialization failed: {detail}")

    @staticmethod
    async def _run_process(
        command: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return {"returncode": None, "output_tail": f"Timed out after {timeout:g}s."}
        return {
            "returncode": process.returncode,
            "output_tail": stdout.decode("utf-8", errors="replace")[-8000:],
        }

    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        normalized = PurePosixPath(value.replace("\\", "/"))
        if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
            raise ValueError(f"Unsafe autonomous project path: {value}")
        path = normalized.as_posix()
        if path.startswith((".", "/")) or any(part.startswith(".") for part in normalized.parts):
            raise ValueError(f"Hidden autonomous project path is not allowed: {value}")
        return path

    @classmethod
    def _safe_project_name(cls, value: str, project_id: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:55]
        if not normalized or not normalized[0].isalpha():
            normalized = f"eck-project-{project_id[-8:]}"
        if len(normalized) < 3:
            normalized = f"{normalized}-eck"
        if not cls._safe_name_pattern.fullmatch(normalized):
            normalized = f"eck-project-{project_id[-8:]}"
        return normalized

    def _project_dir(self, project_id: str) -> Path:
        if not self._project_id_pattern.fullmatch(project_id):
            raise ValueError("Invalid autonomous project ID.")
        path = (self.root / project_id).resolve()
        path.relative_to(self.root.resolve())
        return path

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
    def _gh_executable() -> str | None:
        located = shutil.which("gh")
        if located:
            return located
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHub CLI" / "gh.exe"
        program_files = (
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "GitHub CLI"
            / "gh.exe"
        )
        for path in (local, program_files):
            try:
                if path.is_file():
                    return str(path)
            except OSError:
                continue
        return None

    @staticmethod
    def _write_manifest(project_dir: Path, manifest: dict[str, Any]) -> None:
        AutonomousProjectLabService._write_json(project_dir / "manifest.json", manifest)

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path}")
        return value

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                return {}
            value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)
