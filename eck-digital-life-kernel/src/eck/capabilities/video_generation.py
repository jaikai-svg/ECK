from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.capabilities.image_generation import ImageGenerationCapability
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence


class VideoGenerationCapability(Capability):
    definition = CapabilityDefinition(
        name="video.generate",
        description=(
            "Generate a local first frame and animate it with the official FramePack "
            "image-to-video runtime, preserving artifact hashes and execution evidence."
        ),
        default_risk=RiskLevel.MEDIUM,
        deterministic=False,
    )
    _sexual_terms = re.compile(
        r"\b(nude|naked|erotic|porn(?:ographic)?|sexual|sex scene|adult content)\b|"
        r"裸體|全裸|情色|色情|性愛|成人內容",
        re.IGNORECASE,
    )
    _minor_terms = re.compile(
        r"\b(child|children|kid|kids|minor|underage|preteen|teenager|loli|shota)\b|"
        r"兒童|小孩|未成年|幼童|蘿莉|正太",
        re.IGNORECASE,
    )
    _prohibited_terms = re.compile(
        r"\b(rape|non[- ]?consensual|forced sex|sexual assault|bestiality|zoophilia|animal sex)\b|"
        r"強姦|強暴|非自願|性侵|獸交|人獸性交",
        re.IGNORECASE,
    )

    def __init__(
        self,
        settings: Settings,
        image_generation: ImageGenerationCapability,
    ) -> None:
        self.settings = settings
        self.image_generation = image_generation
        self._lock = asyncio.Lock()
        self._stage = "idle"
        self._started_at: str | None = None

    def status(self) -> dict[str, Any]:
        cache = self.settings.framepack_source_dir / "hf_download" / "hub"
        models = {
            "hunyuan_video": cache / "models--hunyuanvideo-community--HunyuanVideo",
            "siglip": cache / "models--lllyasviel--flux_redux_bfl",
            "framepack_i2v": cache / "models--lllyasviel--FramePackI2V_HY",
        }
        checks = {
            "python": self.settings.video_engine_python.is_file(),
            "worker": self.settings.video_engine_script.is_file(),
            "source": (self.settings.framepack_source_dir / "demo_gradio.py").is_file(),
            **{name: path.is_dir() for name, path in models.items()},
        }
        total_ram_gb, available_ram_gb = self._memory_status_gb()
        memory_ready = (
            total_ram_gb is None
            or available_ram_gb is None
            or (
                total_ram_gb >= self.settings.video_min_system_ram_gb
                and available_ram_gb >= self.settings.video_min_available_ram_gb
            )
        )
        installed = all(checks.values())
        available = self.settings.video_generation_enabled and installed and memory_ready
        return {
            "enabled": self.settings.video_generation_enabled,
            "installed": installed,
            "available": available,
            "backend": "framepack",
            "model": "lllyasviel/FramePackI2V_HY",
            "local_only": True,
            "paid_api": False,
            "checks": checks,
            "resources": {
                "system_ram_gb": total_ram_gb,
                "available_ram_gb": available_ram_gb,
                "minimum_system_ram_gb": self.settings.video_min_system_ram_gb,
                "minimum_available_ram_gb": self.settings.video_min_available_ram_gb,
                "ready": memory_ready,
                "detail": (
                    "FramePack is installed, but this session does not have enough system "
                    "memory for the verified local profile."
                    if installed and not memory_ready
                    else "Local resource gate passed."
                ),
            },
            "activity": {
                "stage": self._stage,
                "started_at": self._started_at,
                "busy": self._stage != "idle",
            },
            "quality": {
                "seconds": self.settings.video_default_seconds,
                "fps": 30,
                "steps": self.settings.video_generation_steps,
                "teacache": self.settings.video_teacache_enabled,
            },
            "content_policy": {
                "legal_adult_content": self.settings.video_adult_content_enabled,
                "minor_sexual_content": False,
                "nonconsensual_sexual_content": False,
                "bestiality": False,
                "safeguard_bypass": False,
            },
        }

    @staticmethod
    def _memory_status_gb() -> tuple[float | None, float | None]:
        if os.name != "nt":
            sysconf_value = getattr(os, "sysconf", None)
            if not callable(sysconf_value):
                return None, None
            sysconf = cast(Callable[[str], int], sysconf_value)
            try:
                page_size = sysconf("SC_PAGE_SIZE")
                total_pages = sysconf("SC_PHYS_PAGES")
                available_pages = sysconf("SC_AVPHYS_PAGES")
            except (AttributeError, OSError, ValueError):
                return None, None
            scale = float(1024**3)
            return (
                round(float(page_size * total_pages) / scale, 2),
                round(float(page_size * available_pages) / scale, 2),
            )

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        memory_status = kernel32.GlobalMemoryStatusEx
        memory_status.argtypes = [ctypes.POINTER(MemoryStatus)]
        memory_status.restype = ctypes.c_int
        if not memory_status(ctypes.byref(status)):
            return None, None
        scale = float(1024**3)
        return (
            round(float(status.total_physical) / scale, 2),
            round(float(status.available_physical) / scale, 2),
        )

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        if action.operation != "generate":
            return self._failure(action, started, "Video generation supports only 'generate'.")
        status = self.status()
        if not status["available"]:
            return self._failure(
                action,
                started,
                "The local FramePack runtime is not ready.",
                status,
            )
        prompt = str(action.payload.get("user_request") or action.payload.get("prompt", "")).strip()
        try:
            self._validate_request_policy(prompt)
        except ValueError as exc:
            return self._failure(action, started, str(exc))
        if len(prompt) < 3:
            return self._failure(action, started, "A descriptive video prompt is required.")

        async with self._lock:
            try:
                input_path, initial_metadata = await self._input_image(action, prompt)
                self._set_stage("releasing_image_gpu")
                await self.image_generation.stop_forge()
                self._set_stage("generating_video")
                video_id = new_id("video")
                output_path = (self.settings.video_output_dir / f"{video_id}.mp4").resolve()
                report = await self._run_worker(
                    {
                        "source_dir": str(self.settings.framepack_source_dir.resolve()),
                        "input_image": str(input_path),
                        "prompt": prompt,
                        "negative_prompt": str(action.payload.get("negative_prompt", "")),
                        "seconds": min(
                            10.0,
                            max(
                                1.0,
                                float(
                                    action.payload.get(
                                        "seconds",
                                        self.settings.video_default_seconds,
                                    )
                                ),
                            ),
                        ),
                        "steps": self.settings.video_generation_steps,
                        "seed": int(action.payload.get("seed", 31337)),
                        "gpu_memory_preservation": 6.0,
                        "use_teacache": self.settings.video_teacache_enabled,
                        "mp4_crf": 16,
                    },
                    output_path,
                )
                self._set_stage("verifying_artifact")
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                return self._failure(action, started, str(exc))
            finally:
                self._set_stage("idle")

        if not report.get("success") or not output_path.is_file():
            return self._failure(
                action,
                started,
                str(report.get("detail") or "FramePack did not produce a video."),
                report,
            )
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        metadata = report.get("metadata", {})
        if not isinstance(metadata, dict):
            return self._failure(action, started, "FramePack returned invalid metadata.")
        metadata["initial_image"] = initial_metadata
        relative = output_path.relative_to(self.settings.workspace_dir.resolve()).as_posix()
        finished = utc_now()
        elapsed = round((finished - started).total_seconds(), 3)
        metadata["total_elapsed_seconds"] = elapsed
        output = {
            "artifact": output_path.name,
            "artifact_path": relative,
            "artifact_url": f"/video-artifacts/{output_path.name}",
            "metadata": metadata,
            "metrics": {
                "completed": True,
                "bytes": output_path.stat().st_size,
                "sha256": digest,
                "seconds": metadata.get("seconds"),
            },
            "skill_fingerprint": "video.generate:framepack-i2v-hy:v1",
            "skill_name": "本機影片生成：FramePack I2V",
            "skill_procedure": {
                "backend": "framepack",
                "model": "lllyasviel/FramePackI2V_HY",
                "steps": metadata.get("steps"),
                "teacache": metadata.get("teacache"),
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
                    claim="The local FramePack worker wrote and hashed an MP4 artifact.",
                    payload={
                        "artifact": output_path.name,
                        "bytes": output_path.stat().st_size,
                        "sha256": digest,
                        "model": metadata.get("model"),
                    },
                ),
            ),
            reversible=True,
            cost_units=max(1.0, elapsed),
            started_at=started,
            finished_at=finished,
        )

    async def _input_image(
        self,
        action: ActionProposal,
        prompt: str,
    ) -> tuple[Path, dict[str, Any]]:
        requested = str(action.payload.get("input_image", "")).strip()
        if requested:
            path = Path(requested)
            if not path.is_absolute():
                path = self.settings.workspace_dir / path
            path = path.resolve()
            path.relative_to(self.settings.workspace_dir.resolve())
            if not path.is_file():
                raise FileNotFoundError(path)
            return path, {"source": "provided", "path": str(path)}
        self._set_stage("generating_first_frame")
        result = await self.image_generation.execute(
            ActionProposal(
                capability="image.generate",
                operation="generate",
                payload={
                    "user_request": f"Create a cinematic first frame for this video: {prompt}",
                    "width": 640,
                    "height": 384,
                },
                declared_risk=RiskLevel.MEDIUM,
                reversible=True,
                estimated_cost_units=60,
            )
        )
        if not result.success:
            raise RuntimeError(str(result.output.get("error", "First-frame generation failed.")))
        path = self.settings.workspace_dir / str(result.output["artifact_path"])
        return path.resolve(), {
            "source": "image.generate",
            "path": result.output["artifact_path"],
            "metadata": result.output.get("metadata", {}),
        }

    async def _run_worker(self, request: dict[str, Any], output_path: Path) -> dict[str, Any]:
        request_path = output_path.with_suffix(".request.json")
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            str(self.settings.video_engine_python.resolve()),
            str(self.settings.video_engine_script.resolve()),
            "--request",
            str(request_path),
            "--output",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.video_generation_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("The FramePack worker exceeded its generation timeout.") from None
        finally:
            request_path.unlink(missing_ok=True)
        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
        try:
            report = json.loads(lines[-1]) if lines else {}
        except ValueError as exc:
            raise RuntimeError("FramePack returned invalid JSON.") from exc
        if process.returncode != 0 and not report:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail[-2000:] or "FramePack worker failed.")
        return report if isinstance(report, dict) else {}

    def _validate_request_policy(self, request: str) -> None:
        sexual = bool(self._sexual_terms.search(request))
        if sexual and self._minor_terms.search(request):
            raise ValueError("Sexual content involving minors is permanently prohibited.")
        if self._prohibited_terms.search(request):
            raise ValueError("Non-consensual or animal sexual content is permanently prohibited.")
        if sexual and not self.settings.video_adult_content_enabled:
            raise ValueError("Legal adult video generation is disabled by configuration.")

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        self._started_at = None if stage == "idle" else utc_now().isoformat()

    def _failure(
        self,
        action: ActionProposal,
        started: Any,
        detail: str,
        extra: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        output: dict[str, Any] = {"error": detail, "metrics": {"completed": False}}
        if extra:
            output["engine"] = extra
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=False,
            output=output,
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="The local video worker did not produce a verified artifact.",
                    payload={"detail": detail},
                ),
            ),
            reversible=True,
            cost_units=0,
            started_at=started,
            finished_at=utc_now(),
        )
