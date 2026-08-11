from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from eck.core.time import utc_now
from eck.services.project_lab_components.base import ProjectLabMixinBase
from eck.services.project_lab_components.github_policy import GitHubCommandPolicy


class ProjectLabSupportMixin(ProjectLabMixinBase):
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
        safe_directory = f"safe.directory={source_dir.resolve()}"
        commands = (
            ["git", "-c", safe_directory, "init", "-b", "main"],
            ["git", "-c", safe_directory, "add", "."],
            [
                "git",
                "-c",
                safe_directory,
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
        GitHubCommandPolicy.validate(command)
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

    def _gh_executable(self) -> str | None:
        located = shutil.which("gh")
        if located:
            return located
        workspace = self.settings.workspace_dir / "tools" / "gh" / "bin" / "gh.exe"
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHub CLI" / "gh.exe"
        program_files = (
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "GitHub CLI"
            / "gh.exe"
        )
        for path in (workspace, local, program_files):
            try:
                if path.is_file():
                    return str(path.resolve())
            except OSError:
                continue
        return None

    @staticmethod
    def _write_manifest(project_dir: Path, manifest: dict[str, Any]) -> None:
        ProjectLabSupportMixin._write_json(project_dir / "manifest.json", manifest)

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
