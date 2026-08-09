from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from eck.config import Settings
from eck.core.ids import new_id
from eck.domain.models import RuntimeSkillRecord


class DockerSkillWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._health_cache: dict[str, Any] | None = None
        self._health_checked_at = 0.0

    async def health(self) -> dict[str, Any]:
        now = asyncio.get_running_loop().time()
        if self._health_cache is not None and now - self._health_checked_at < 30:
            return self._health_cache
        executable = shutil.which("docker")
        if not self.settings.skill_worker_enabled:
            return self._cache_health(
                now, {"available": False, "detail": "Docker skill workers are disabled."}
            )
        if executable is None:
            return self._cache_health(
                now, {"available": False, "detail": "Docker CLI is not installed."}
            )
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "version",
                "--format",
                "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except (TimeoutError, OSError) as exc:
            return self._cache_health(now, {"available": False, "detail": str(exc)})
        detail = (stdout or stderr).decode("utf-8", errors="replace").strip()
        return self._cache_health(
            now,
            {
                "available": process.returncode == 0,
                "detail": detail or "Docker daemon did not respond.",
                "image": self.settings.skill_worker_image,
            },
        )

    def _cache_health(self, checked_at: float, value: dict[str, Any]) -> dict[str, Any]:
        self._health_cache = value
        self._health_checked_at = checked_at
        return value

    async def image_available(self) -> bool:
        return bool((await self.image_status())["available"])

    async def image_status(self) -> dict[str, Any]:
        latest: dict[str, Any] = {
            "available": False,
            "image": self.settings.skill_worker_image,
            "detail": "Docker image inspection was not attempted.",
        }
        for attempt in range(3):
            latest = await self._inspect_image()
            if latest["available"]:
                return latest
            if attempt < 2:
                await asyncio.sleep(0.75 * (attempt + 1))
        return latest

    async def _inspect_image(self) -> dict[str, Any]:
        executable = shutil.which("docker")
        if executable is None:
            return {
                "available": False,
                "image": self.settings.skill_worker_image,
                "detail": "Docker CLI is not installed or not present on PATH.",
            }
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "image",
                "inspect",
                self.settings.skill_worker_image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except (OSError, TimeoutError) as exc:
            return {
                "available": False,
                "image": self.settings.skill_worker_image,
                "executable": executable,
                "detail": f"{type(exc).__name__}: {exc}",
            }
        detail = stderr.decode("utf-8", errors="replace").strip()
        return {
            "available": process.returncode == 0,
            "image": self.settings.skill_worker_image,
            "executable": executable,
            "returncode": process.returncode,
            "detail": detail[-2000:] if detail else "Image is available.",
        }

    async def build_image(self, project_root: Path) -> dict[str, Any]:
        executable = shutil.which("docker")
        if executable is None:
            return {"success": False, "detail": "Docker CLI is not installed."}
        process = await asyncio.create_subprocess_exec(
            executable,
            "build",
            "--file",
            str(project_root / "docker" / "skill-worker" / "Dockerfile"),
            "--tag",
            self.settings.skill_worker_image,
            str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=self.settings.skill_worker_timeout_seconds
        )
        detail = stdout.decode("utf-8", errors="replace")[-8000:]
        self._health_cache = None
        return {"success": process.returncode == 0, "detail": detail}

    async def validate(self, skill: RuntimeSkillRecord) -> dict[str, Any]:
        return await self._run(skill, mode="validate", payload={})

    async def execute(
        self,
        skill: RuntimeSkillRecord,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if operation not in skill.manifest.operations:
            return {"success": False, "error": f"Unsupported operation: {operation}"}
        return await self._run(
            skill,
            mode="execute",
            payload={"operation": operation, "payload": payload},
        )

    async def _run(
        self,
        skill: RuntimeSkillRecord,
        *,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        health = await self.health()
        if not health["available"]:
            return {"success": False, "worker_unavailable": True, "detail": health["detail"]}
        if not await self.image_available():
            return {
                "success": False,
                "worker_unavailable": True,
                "detail": f"Worker image {self.settings.skill_worker_image!r} is not built.",
            }

        source_dir = Path(skill.source_dir).resolve()
        if not source_dir.is_dir():
            return {"success": False, "detail": f"Skill source directory is missing: {source_dir}"}

        self.settings.workspace_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="eck-worker-", dir=self.settings.workspace_dir
        ) as temp_dir_value:
            temp_dir = Path(temp_dir_value)
            request_dir = temp_dir / "request"
            output_dir = temp_dir / "output"
            request_dir.mkdir()
            output_dir.mkdir()
            (request_dir / "manifest.json").write_text(
                skill.manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            (request_dir / "request.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            report = await self._docker_run(
                source_dir=source_dir,
                request_dir=request_dir,
                output_dir=output_dir,
                mode=mode,
                allow_network=(
                    self.settings.network_enabled
                    and (
                        "network:public" in skill.manifest.permissions
                        or bool(skill.manifest.dependencies)
                    )
                ),
            )
            relative_artifacts = [
                path.relative_to(output_dir)
                for path in output_dir.rglob("*")
                if path.is_file()
            ]
            if mode == "execute" and relative_artifacts:
                artifact_dir = (
                    self.settings.workspace_dir
                    / "runtime_artifacts"
                    / new_id("skill-run")
                )
                shutil.copytree(output_dir, artifact_dir)
                report["artifact_dir"] = artifact_dir.relative_to(
                    self.settings.workspace_dir
                ).as_posix()
                report["artifacts"] = [
                    (artifact_dir / relative).relative_to(
                        self.settings.workspace_dir
                    ).as_posix()
                    for relative in relative_artifacts
                ]
            else:
                report["artifacts"] = [path.as_posix() for path in relative_artifacts]
            return report

    async def _docker_run(
        self,
        *,
        source_dir: Path,
        request_dir: Path,
        output_dir: Path,
        mode: str,
        allow_network: bool,
    ) -> dict[str, Any]:
        executable = shutil.which("docker")
        assert executable is not None
        command = (
            executable,
            "run",
            "--rm",
            "--read-only",
            "--network",
            "bridge" if allow_network else "none",
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
            f"type=bind,source={source_dir},target=/skill,readonly",
            "--mount",
            f"type=bind,source={request_dir},target=/request,readonly",
            "--mount",
            f"type=bind,source={output_dir},target=/output",
            self.settings.skill_worker_image,
            mode,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.settings.skill_worker_timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return {"success": False, "detail": "Skill worker exceeded its time limit."}
        output = stdout.decode("utf-8", errors="replace").strip().splitlines()
        detail = stderr.decode("utf-8", errors="replace")[-4000:]
        if output:
            try:
                result = json.loads(output[-1])
                if isinstance(result, dict):
                    result.setdefault("detail", detail)
                    return result
            except json.JSONDecodeError:
                pass
        return {
            "success": False,
            "detail": detail or "The worker returned no structured result.",
            "stdout": "\n".join(output)[-4000:],
        }
