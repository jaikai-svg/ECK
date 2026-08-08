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

import httpx

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
        total_ram_gb, available_ram_gb = self._memory_status_gb()
        backends = {
            "framepack": self._framepack_status(total_ram_gb, available_ram_gb),
            "cogvideox": self._cogvideo_status(total_ram_gb, available_ram_gb),
        }
        configured = self.settings.video_backend
        if configured == "auto":
            backend = next(
                (name for name in ("framepack", "cogvideox") if backends[name]["available"]),
                "framepack",
            )
        else:
            backend = configured
        selected = dict(backends[backend])
        selected.update(
            {
                "enabled": self.settings.video_generation_enabled,
                "backend": backend,
                "configured_backend": configured,
                "local_only": True,
                "paid_api": False,
                "backends": backends,
                "activity": {
                    "stage": self._stage,
                    "started_at": self._started_at,
                    "busy": self._stage != "idle",
                },
                "content_policy": {
                    "legal_adult_content": self.settings.video_adult_content_enabled,
                    "minor_sexual_content": False,
                    "nonconsensual_sexual_content": False,
                    "bestiality": False,
                    "safeguard_bypass": False,
                },
            }
        )
        return selected

    def _framepack_status(
        self,
        total_ram_gb: float | None,
        available_ram_gb: float | None,
    ) -> dict[str, Any]:
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
            "installed": installed,
            "available": available,
            "model": "lllyasviel/FramePackI2V_HY",
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
            "quality": {
                "seconds": self.settings.video_default_seconds,
                "fps": 30,
                "steps": self.settings.video_generation_steps,
                "teacache": self.settings.video_teacache_enabled,
            },
        }

    def _cogvideo_status(
        self,
        total_ram_gb: float | None,
        available_ram_gb: float | None,
    ) -> dict[str, Any]:
        model = self.settings.cogvideo_model_dir
        report = self._load_cogvideo_smoke_report()
        checks = {
            "python": self.settings.cogvideo_python.is_file(),
            "worker": self.settings.cogvideo_script.is_file(),
            "model_index": (model / "model_index.json").is_file(),
            "text_encoder": (model / "text_encoder" / "config.json").is_file(),
            "transformer": (model / "transformer" / "config.json").is_file(),
            "vae": (model / "vae" / "config.json").is_file(),
            "smoke_test": bool(report.get("verified")),
        }
        installed = all(value for name, value in checks.items() if name != "smoke_test")
        memory_ready = (
            total_ram_gb is None
            or available_ram_gb is None
            or (
                total_ram_gb >= self.settings.cogvideo_min_system_ram_gb
                and available_ram_gb >= self.settings.cogvideo_min_available_ram_gb
            )
        )
        available = (
            self.settings.video_generation_enabled
            and installed
            and checks["smoke_test"]
            and memory_ready
        )
        return {
            "installed": installed,
            "available": available,
            "model": "zai-org/CogVideoX-2b",
            "checks": checks,
            "resources": {
                "system_ram_gb": total_ram_gb,
                "available_ram_gb": available_ram_gb,
                "minimum_system_ram_gb": self.settings.cogvideo_min_system_ram_gb,
                "minimum_available_ram_gb": self.settings.cogvideo_min_available_ram_gb,
                "ready": memory_ready,
                "detail": (
                    "CogVideoX is installed but current free system memory is below "
                    "the locally verified profile."
                    if installed and not memory_ready
                    else "Local low-memory CogVideoX profile passed."
                ),
            },
            "quality": {
                "seconds": self.settings.video_default_seconds,
                "fps": 8,
                "steps": self.settings.video_generation_steps,
                "precision": "fp16",
                "offload": "sequential_cpu",
            },
            "verification": report,
        }

    def _load_cogvideo_smoke_report(self) -> dict[str, Any]:
        try:
            report = json.loads(
                self.settings.cogvideo_smoke_report.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        if not isinstance(report, dict) or report.get("model") != "zai-org/CogVideoX-2b":
            return {}
        return report

    def skill_graph_snapshot(self) -> dict[str, Any]:
        status = self.status()
        backend = str(status.get("backend", self.settings.video_backend))
        model = str(status.get("model", "local video model"))
        verification = status.get("verification", {})
        verified = bool(
            backend == "cogvideox"
            and isinstance(verification, dict)
            and verification.get("verified")
        )
        acquired = bool(status.get("enabled") and status.get("installed") and verified)
        sources: list[dict[str, Any]] = []
        if verified:
            sources.append(
                {
                    "title": "CogVideoX 本機低記憶體煙霧測試報告",
                    "reference": str(self.settings.cogvideo_smoke_report),
                    "source_type": "verification",
                    "verified": verified,
                }
            )
        return {
            "fingerprint": "video.generate:cogvideox-2b:v1",
            "title": "本機影片生成：CogVideoX-2B",
            "capability": self.definition.name,
            "description": (
                "使用 FP16、循序 CPU offload、VAE slicing 與 tiling 在本機生成影片。"
            ),
            "acquired": acquired,
            "runtime_available": bool(status.get("available")),
            "procedure": {
                "backend": backend,
                "model": model,
                **dict(status.get("quality", {})),
            },
            "verification": verification if isinstance(verification, dict) else {},
            "sources": sources,
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
                "The selected local video runtime is not ready.",
                status,
            )
        backend = str(status.get("backend", "framepack"))
        prompt = str(action.payload.get("user_request") or action.payload.get("prompt", "")).strip()
        try:
            self._validate_request_policy(prompt)
        except ValueError as exc:
            return self._failure(action, started, str(exc))
        if len(prompt) < 3:
            return self._failure(action, started, "A descriptive video prompt is required.")

        async with self._lock:
            try:
                seconds = min(
                    6.0 if backend == "cogvideox" else 10.0,
                    max(
                        1.0,
                        float(
                            action.payload.get(
                                "seconds",
                                self.settings.video_default_seconds,
                            )
                        ),
                    ),
                )
                initial_metadata: dict[str, Any]
                input_path: Path | None
                if backend == "framepack":
                    input_path, initial_metadata = await self._input_image(action, prompt)
                else:
                    input_path = None
                    initial_metadata = {"source": "text-to-video"}
                self._set_stage("releasing_gpu_workers")
                await self.image_generation.stop_forge()
                await self._release_ollama_gpu()
                self._set_stage("generating_video")
                video_id = new_id("video")
                output_path = (self.settings.video_output_dir / f"{video_id}.mp4").resolve()
                request: dict[str, Any] = {
                    "backend": backend,
                    "prompt": prompt,
                    "negative_prompt": str(action.payload.get("negative_prompt", "")),
                    "seconds": seconds,
                    "steps": self.settings.video_generation_steps,
                    "seed": int(action.payload.get("seed", 31337)),
                }
                if backend == "cogvideox":
                    frame_groups = max(1, min(6, round(seconds)))
                    request.update(
                        {
                            "model_dir": str(self.settings.cogvideo_model_dir.resolve()),
                            "frames": frame_groups * 8 + 1,
                            "fps": 8,
                            "guidance_scale": float(
                                action.payload.get("guidance_scale", 6.0)
                            ),
                        }
                    )
                else:
                    assert input_path is not None
                    request.update(
                        {
                        "source_dir": str(self.settings.framepack_source_dir.resolve()),
                        "input_image": str(input_path),
                        "gpu_memory_preservation": 6.0,
                        "use_teacache": self.settings.video_teacache_enabled,
                        "mp4_crf": 16,
                        }
                    )
                report = await self._run_worker(request, output_path)
                self._set_stage("verifying_artifact")
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                return self._failure(action, started, str(exc))
            finally:
                self._set_stage("idle")

        if not report.get("success") or not output_path.is_file():
            return self._failure(
                action,
                started,
                str(report.get("detail") or f"{backend} did not produce a video."),
                report,
            )
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        metadata = report.get("metadata", {})
        if not isinstance(metadata, dict):
            return self._failure(action, started, f"{backend} returned invalid metadata.")
        metadata["generation_input"] = initial_metadata
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
            "skill_fingerprint": (
                "video.generate:cogvideox-2b:v1"
                if backend == "cogvideox"
                else "video.generate:framepack-i2v-hy:v1"
            ),
            "skill_name": (
                "CogVideoX-2B local text-to-video"
                if backend == "cogvideox"
                else "FramePack local image-to-video"
            ),
            "skill_procedure": {
                "backend": backend,
                "model": metadata.get("model"),
                "steps": metadata.get("steps"),
                "offload": metadata.get("offload"),
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
                    claim=f"The local {backend} worker wrote and hashed an MP4 artifact.",
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

    async def _release_ollama_gpu(self) -> None:
        if self.settings.brain_provider != "ollama":
            return
        endpoint = f"{self.settings.ollama_base_url.rstrip('/')}/api/generate"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    endpoint,
                    json={"model": self.settings.ollama_model, "keep_alive": 0},
                )
        except httpx.HTTPError:
            return

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
        backend = str(request.get("backend", "framepack"))
        python = (
            self.settings.cogvideo_python
            if backend == "cogvideox"
            else self.settings.video_engine_python
        )
        script = (
            self.settings.cogvideo_script
            if backend == "cogvideox"
            else self.settings.video_engine_script
        )
        process = await asyncio.create_subprocess_exec(
            str(python.resolve()),
            str(script.resolve()),
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
            raise RuntimeError(f"The {backend} worker exceeded its generation timeout.") from None
        finally:
            request_path.unlink(missing_ok=True)
        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
        try:
            report = json.loads(lines[-1]) if lines else {}
        except ValueError as exc:
            raise RuntimeError(f"{backend} returned invalid JSON.") from exc
        if process.returncode != 0 and not report:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail[-2000:] or f"{backend} worker failed.")
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
