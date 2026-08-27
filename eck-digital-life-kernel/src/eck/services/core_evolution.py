from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.models import CoreCandidateRequest
from eck.events.bus import EventBus
from eck.services.evolution_policy import EvolutionProtectedSurfacePolicy
from eck.services.evolution_transaction import EvolutionTransactionService
from eck.services.self_model import RepositorySelfModelService


class CoreEvolutionLabService:
    _new_file_roots = ("src/eck/experimental/", "tests/")

    def __init__(
        self,
        settings: Settings,
        events: EventBus,
        brain: BrainProvider,
        self_model: RepositorySelfModelService,
        transactions: EvolutionTransactionService | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.events = events
        self.brain = brain
        self.self_model = self_model
        self.transactions = transactions
        self.project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        self.protected_policy = EvolutionProtectedSurfacePolicy(self.project_root)
        self.metadata_root = settings.evolution_dir / "core_candidates"
        self.metadata_root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        candidates = self.list_candidates()
        counts: dict[str, int] = {}
        for item in candidates:
            state = str(item.get("status", "unknown"))
            counts[state] = counts.get(state, 0) + 1
        return {
            "enabled": self.settings.core_evolution_enabled,
            "candidate_count": len(candidates),
            "status_counts": counts,
            "latest": candidates[0] if candidates else None,
            "fixed_evaluator": "p4-fixed-v1",
            "held_out_evaluation": "required_before_human_approved_activation",
            "live_core_mutation": False,
            "restart_activation_supported": self.transactions is not None,
            "activation_policy": "human_approval_required_after_all_fixed_gates_pass",
            "truthfulness": (
                "A fixed-gate pass is not an improvement claim. Activation additionally requires "
                "a pre-registered held-out evaluation, explicit human approval, an exact Git "
                "tree match, a graceful restart, and a startup receipt."
            ),
        }

    def list_candidates(self) -> list[dict[str, Any]]:
        candidates = []
        for path in self.metadata_root.glob("*/manifest.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                candidates.append(value)
        return sorted(candidates, key=lambda item: str(item.get("created_at", "")), reverse=True)

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        path = self._candidate_metadata_dir(candidate_id) / "manifest.json"
        if not path.is_file():
            raise KeyError(f"Unknown core candidate: {candidate_id}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Core candidate manifest must be a JSON object.")
        return value

    async def create_candidate(self, request: CoreCandidateRequest) -> dict[str, Any]:
        if not self.settings.core_evolution_enabled:
            raise RuntimeError("Core candidate evolution is disabled.")
        git_root = self._git_root()
        if git_root is None:
            raise RuntimeError("Core candidates require a Git repository.")
        source_commit = self._git(git_root, "rev-parse", "HEAD").strip()
        if self._git(git_root, "status", "--porcelain").strip():
            raise RuntimeError("Core candidate creation requires a clean source tree.")
        repository_model = self.self_model.refresh()
        target_files = tuple(self._validate_relative_path(path) for path in request.target_files)
        protected_paths = self.protected_policy.assert_candidate_allowed(list(target_files))
        for relative in target_files:
            target = self.project_root / relative
            if not target.is_file():
                raise ValueError(f"Candidate target does not exist: {relative}")

        candidate_id = new_id("core-candidate")
        metadata_dir = self._candidate_metadata_dir(candidate_id)
        metadata_dir.mkdir(parents=True, exist_ok=False)
        checkout_root = self._checkout_root(git_root) / candidate_id
        checkout_root.parent.mkdir(parents=True, exist_ok=True)
        self._git(git_root, "worktree", "add", "--detach", str(checkout_root), source_commit)
        candidate_project = checkout_root / self.project_root.relative_to(git_root)
        try:
            changes, rationale, proposed_tests, model = await self._draft_changes(
                request,
                candidate_project,
                target_files,
            )
            changed_files = self._apply_changes(
                request,
                candidate_project,
                target_files,
                changes,
            )
            changed_paths = [str(item["path"]) for item in changed_files]
            self._git(candidate_project, "add", "--", *changed_paths)
            staged_paths = {
                item
                for item in self._git(
                    candidate_project,
                    "diff",
                    "--cached",
                    "--name-only",
                    "--relative",
                    "--",
                    ".",
                ).splitlines()
                if item
            }
            if staged_paths != set(changed_paths):
                raise RuntimeError("Candidate staging included missing or unattributed paths.")
            patch = self._git(
                candidate_project,
                "diff",
                "--cached",
                "--binary",
                "--no-ext-diff",
                "--",
                ".",
            )
            patch_path = metadata_dir / "candidate.patch"
            patch_path.write_text(patch, encoding="utf-8", newline="")
            patch_sha256 = hashlib.sha256(patch_path.read_bytes()).hexdigest()
            manifest = {
                "schema_version": "eck-core-candidate.v1",
                "candidate_id": candidate_id,
                "objective": request.objective,
                "source_commit": source_commit,
                "source_tree_sha256": repository_model.get("source_tree_sha256"),
                "checkout_path": str(checkout_root),
                "project_path": str(candidate_project),
                "target_files": list(target_files),
                "changed_files": changed_files,
                "rationale": rationale,
                "proposed_tests": proposed_tests,
                "model": model,
                "patch_sha256": patch_sha256,
                "candidate_tree_sha": self._git(candidate_project, "write-tree").strip(),
                "protected_paths": protected_paths,
                "status": "drafted",
                "created_at": utc_now().isoformat(),
                "updated_at": utc_now().isoformat(),
                "fixed_evaluator": "p4-fixed-v1",
                "requires_human_approval": True,
                "activated": False,
            }
            self._write_manifest(metadata_dir, manifest)
            if self.transactions is not None:
                self.transactions.observe_candidate(manifest)
            await self.events.publish(
                "CoreCandidateDrafted",
                candidate_id,
                {
                    "source_commit": source_commit,
                    "changed_files": [item["path"] for item in changed_files],
                    "model": model,
                },
                correlation_id=candidate_id,
            )
        except Exception:
            self._remove_worktree(git_root, checkout_root)
            raise
        return await self.validate_candidate(candidate_id)

    async def validate_candidate(self, candidate_id: str) -> dict[str, Any]:
        manifest = self.get_candidate(candidate_id)
        candidate_project = Path(str(manifest["project_path"]))
        if not candidate_project.is_dir():
            raise FileNotFoundError("The isolated candidate checkout is missing.")
        changed_python = [
            str(item["path"])
            for item in manifest.get("changed_files", [])
            if str(item.get("path", "")).endswith(".py")
        ]
        gates = [
            await self._run_gate(
                "compile",
                [sys.executable, "-m", "compileall", "-q", "src/eck"],
                candidate_project,
            ),
            await self._run_gate(
                "ruff",
                [sys.executable, "-m", "ruff", "check", *(changed_python or ["src/eck"])],
                candidate_project,
            ),
            await self._run_gate(
                "mypy",
                [sys.executable, "-m", "mypy", "src/eck"],
                candidate_project,
            ),
            await self._run_gate(
                "full_regression",
                [sys.executable, "-m", "pytest", "-q"],
                candidate_project,
            ),
        ]
        passed = all(item["status"] == "passed" for item in gates)
        manifest.update(
            {
                "status": "validated_awaiting_human" if passed else "rejected_by_fixed_gates",
                "updated_at": utc_now().isoformat(),
                "validation": {
                    "evaluator": "p4-fixed-v1",
                    "passed": passed,
                    "gates": gates,
                    "shadow_replay": "full_regression_test_suite",
                    "held_out_tasks": "not_configured",
                },
                "activated": False,
            }
        )
        metadata_dir = self._candidate_metadata_dir(candidate_id)
        self._write_manifest(metadata_dir, manifest)
        if self.transactions is not None:
            self.transactions.observe_candidate(manifest)
        await self.events.publish(
            "CoreCandidateValidated" if passed else "CoreCandidateRejected",
            candidate_id,
            {"passed": passed, "gates": [item["name"] for item in gates]},
            correlation_id=candidate_id,
        )
        return manifest

    async def _draft_changes(
        self,
        request: CoreCandidateRequest,
        candidate_project: Path,
        target_files: tuple[str, ...],
    ) -> tuple[list[dict[str, str]], str, list[str], str]:
        sources = []
        total_chars = 0
        for relative in target_files:
            content = (candidate_project / relative).read_text(encoding="utf-8")
            total_chars += len(content)
            if total_chars > 60000:
                raise ValueError("Candidate source context exceeds the 60,000 character limit.")
            sources.append({"path": relative, "content": content})
        response = await self.brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are drafting one minimal ECK core change inside an isolated Git "
                        "worktree. Return complete replacement file contents, never a prose diff. "
                        "Change only allowed target paths. Preserve public APIs unless the "
                        "objective "
                        "explicitly requires a compatible addition. Add or update tests. Do not "
                        "disable verification, security gates, logging, or human approval."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "allow_new_files": request.allow_new_files,
                            "allowed_targets": list(target_files),
                            "sources": sources,
                            "repository_summary": self.self_model.ensure().get("summary", {}),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            format_schema={
                "type": "object",
                "properties": {
                    "changes": {
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
                    "rationale": {"type": "string"},
                    "tests": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["changes", "rationale", "tests"],
            },
        )
        payload = self._json_object(response.content)
        changes = [
            {"path": str(item.get("path", "")), "content": str(item.get("content", ""))}
            for item in payload.get("changes", [])
            if isinstance(item, dict)
        ]
        if not changes:
            raise ValueError("The model did not produce any candidate file changes.")
        return (
            changes,
            str(payload.get("rationale", ""))[:8000],
            [str(item)[:1000] for item in payload.get("tests", [])],
            response.model,
        )

    def _apply_changes(
        self,
        request: CoreCandidateRequest,
        candidate_project: Path,
        target_files: tuple[str, ...],
        changes: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        allowed = set(target_files)
        records = []
        seen: set[str] = set()
        for change in changes:
            relative = self._validate_relative_path(change["path"])
            if relative in seen:
                raise ValueError(f"Duplicate candidate change: {relative}")
            seen.add(relative)
            target = candidate_project / relative
            is_new = not target.exists()
            if relative not in allowed and not (
                request.allow_new_files
                and is_new
                and relative.startswith(self._new_file_roots)
            ):
                raise ValueError(f"Candidate attempted an unapproved path: {relative}")
            content = change["content"]
            if not content or len(content.encode("utf-8")) > 250_000:
                raise ValueError(f"Candidate file content is empty or too large: {relative}")
            if target.suffix == ".py":
                ast.parse(content)
            before = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            records.append(
                {
                    "path": relative,
                    "new_file": is_new,
                    "before_sha256": before,
                    "after_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )
        return records

    async def _run_gate(
        self,
        name: str,
        command: list[str],
        cwd: Path,
    ) -> dict[str, Any]:
        started = utc_now()
        env = os.environ.copy()
        source_path = str(cwd / "src")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = source_path + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.core_evolution_timeout_seconds,
            )
            output = stdout.decode("utf-8", errors="replace")[-8000:]
            returncode = process.returncode
        except TimeoutError:
            process.kill()
            await process.wait()
            output = f"Timed out after {self.settings.core_evolution_timeout_seconds:g}s."
            returncode = None
        except OSError as exc:
            output = f"{type(exc).__name__}: {exc}"
            returncode = None
        return {
            "name": name,
            "status": "passed" if returncode == 0 else "failed",
            "returncode": returncode,
            "output_tail": output,
            "started_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
        }

    def _candidate_metadata_dir(self, candidate_id: str) -> Path:
        if not re.fullmatch(r"core-candidate_[a-f0-9]{32}", candidate_id):
            raise ValueError("Invalid core candidate ID.")
        path = (self.metadata_root / candidate_id).resolve()
        path.relative_to(self.metadata_root.resolve())
        return path

    @staticmethod
    def _validate_relative_path(value: str) -> str:
        normalized = PurePosixPath(value.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError(f"Unsafe candidate path: {value}")
        relative = normalized.as_posix()
        if not relative.startswith(("src/eck/", "tests/", "docs/")):
            raise ValueError(f"Candidate path is outside approved roots: {relative}")
        return relative

    def _git_root(self) -> Path | None:
        for candidate in (self.project_root, *self.project_root.parents):
            if (candidate / ".git").exists():
                return candidate
        return None

    @staticmethod
    def _checkout_root(git_root: Path) -> Path:
        return git_root.parent / f".{git_root.name}-evolution-worktrees"

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
            raise RuntimeError(detail)
        return result.stdout

    @classmethod
    def _remove_worktree(cls, git_root: Path, checkout_root: Path) -> None:
        with suppress(RuntimeError):
            cls._git(git_root, "worktree", "remove", "--force", str(checkout_root))

    @staticmethod
    def _write_manifest(metadata_dir: Path, manifest: dict[str, Any]) -> None:
        temporary = metadata_dir / "manifest.json.tmp"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(metadata_dir / "manifest.json")

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
