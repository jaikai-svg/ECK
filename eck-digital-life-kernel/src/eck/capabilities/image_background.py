from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence


class ImageBackgroundRemovalCapability(Capability):
    definition = CapabilityDefinition(
        name="image.remove_background",
        description=(
            "Remove an image background locally with rembg and BiRefNet-General, producing "
            "a transparent PNG with hashed evidence."
        ),
        default_risk=RiskLevel.LOW,
        deterministic=False,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        model_path = self.settings.rembg_model_dir / "birefnet-general.onnx"
        checks = {
            "python": self.settings.rembg_python.is_file(),
            "engine": self.settings.rembg_script.is_file(),
            "model": model_path.is_file(),
        }
        return {
            "enabled": self.settings.rembg_enabled,
            "available": self.settings.rembg_enabled and all(checks.values()),
            "worker_warm": bool(self._process and self._process.returncode is None),
            "model": self.settings.rembg_model,
            "provider": "CPUExecutionProvider",
            "checks": checks,
        }

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        if action.operation not in {"remove", "remove_background"}:
            return self._failure(action, started, "Background removal supports only 'remove'.")
        status = self.status()
        if not status["available"]:
            return self._failure(action, started, "The local rembg worker is not ready.")
        try:
            input_path = self._input_path(action.payload.get("artifact_path"))
            output_path = (
                self.settings.image_output_dir / f"{new_id('image_nobg')}.png"
            ).resolve()
            report = await self._run_worker(input_path, output_path)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(action, started, str(exc))
        if not report.get("success") or not output_path.is_file():
            return self._failure(
                action,
                started,
                str(report.get("detail") or "rembg did not produce an artifact."),
            )
        metadata = report.get("metadata", {})
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        relative_path = output_path.relative_to(self.settings.workspace_dir.resolve()).as_posix()
        finished = utc_now()
        metadata["source_artifact"] = input_path.name
        metadata["total_elapsed_seconds"] = round((finished - started).total_seconds(), 3)
        output_path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        output = {
            "artifact": output_path.name,
            "artifact_path": relative_path,
            "artifact_url": f"/artifacts/{output_path.name}",
            "metadata": metadata,
            "metrics": {
                "completed": True,
                "bytes": output_path.stat().st_size,
                "sha256": digest,
            },
            "skill_fingerprint": (
                f"image.remove_background:{str(metadata.get('model') or 'rembg').casefold()}"
            ),
            "skill_name": f"本機背景移除：{metadata.get('model') or 'rembg'}",
            "skill_procedure": {
                "backend": "rembg",
                "model": metadata.get("model") or "rembg",
                "output": "transparent_png",
            },
        }
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=True,
            output=output,
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="The local rembg worker created and hashed a transparent PNG.",
                    payload={
                        "source": input_path.name,
                        "artifact": output_path.name,
                        "sha256": digest,
                    },
                ),
            ),
            reversible=True,
            cost_units=max(1.0, (finished - started).total_seconds()),
            started_at=started,
            finished_at=finished,
        )

    def _input_path(self, value: object) -> Path:
        if value:
            candidate = Path(str(value))
            if not candidate.is_absolute():
                candidate = self.settings.workspace_dir / candidate
            candidate = candidate.resolve()
            try:
                candidate.relative_to(self.settings.image_output_dir.resolve())
            except ValueError as exc:
                raise ValueError("Background removal input must be a generated artifact.") from exc
            if not candidate.is_file():
                raise FileNotFoundError(f"Image artifact not found: {candidate.name}")
            return candidate
        candidates = sorted(
            (
                item
                for item in self.settings.image_output_dir.glob("*.png")
                if not item.name.startswith("image_nobg_")
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError("No generated image is available for background removal.")
        return candidates[0].resolve()

    async def _run_worker(self, input_path: Path, output_path: Path) -> dict[str, Any]:
        async with self._lock:
            process = await self._ensure_process()
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("The rembg worker pipes are unavailable.")
            command = {
                "input": str(input_path),
                "output": str(output_path),
                "model": self.settings.rembg_model,
            }
            process.stdin.write(f"{json.dumps(command)}\n".encode())
            await process.stdin.drain()
            try:
                response = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=self.settings.image_generation_timeout_seconds,
                )
            except TimeoutError as exc:
                await self._stop_process()
                raise RuntimeError("The rembg worker timed out.") from exc
            if not response:
                await self._stop_process()
                raise RuntimeError("The rembg worker stopped without a result.")
        try:
            report = json.loads(response.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise RuntimeError("The rembg worker returned invalid JSON.") from exc
        if not isinstance(report, dict):
            raise RuntimeError("The rembg worker returned an invalid result object.")
        return report

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._process and self._process.returncode is None:
            return self._process
        environment = os.environ.copy()
        environment["U2NET_HOME"] = str(self.settings.rembg_model_dir.resolve())
        environment["OMP_NUM_THREADS"] = str(max(1, min(8, os.cpu_count() or 1)))
        self._process = await asyncio.create_subprocess_exec(
            str(self.settings.rembg_python.resolve()),
            str(self.settings.rembg_script.resolve()),
            "--serve",
            "--model",
            self.settings.rembg_model,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
        return self._process

    async def close(self) -> None:
        async with self._lock:
            await self._stop_process(graceful=True)

    async def _stop_process(self, *, graceful: bool = False) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        if graceful and process.stdin is not None:
            process.stdin.write(b'{"command":"shutdown"}\n')
            await process.stdin.drain()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
                return
            except TimeoutError:
                pass
        process.kill()
        await process.wait()

    def _failure(
        self, action: ActionProposal, started: Any, detail: str
    ) -> CapabilityResult:
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=False,
            output={"error": detail, "metrics": {"completed": False}},
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="The local rembg worker did not produce a verified artifact.",
                    payload={"detail": detail},
                ),
            ),
            reversible=True,
            cost_units=0,
            started_at=started,
            finished_at=utc_now(),
        )
