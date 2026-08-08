from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx

from eck.brain.base import BrainResponse
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
            "Generate local text-to-video with CogVideoX or animate a verified first frame "
            "with FramePack, preserving prompts, artifact hashes, and execution evidence."
        ),
        default_risk=RiskLevel.MEDIUM,
        deterministic=False,
    )
    _sexual_terms = re.compile(
        r"\b(nsfw|nude|naked|erotic|porn(?:ographic)?|sexual|sex scene|adult content)\b|"
        r"裸體|裸露|全裸|情色|色情|性愛|成人內容|陰部|生殖器|乳房裸露",
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
        user_request = str(action.payload.get("user_request", "")).strip()
        prompt = user_request or str(action.payload.get("prompt", "")).strip()
        try:
            self._validate_request_policy(prompt)
        except ValueError as exc:
            return self._failure(action, started, str(exc))
        if len(prompt) < 3:
            return self._failure(action, started, "A descriptive video prompt is required.")
        planner_model: str | None = None
        planner_inference: dict[str, Any] = {}
        negative_prompt = str(action.payload.get("negative_prompt", ""))
        if user_request:
            try:
                plan, response = await self._plan_user_request(user_request)
            except (RuntimeError, httpx.HTTPError) as exc:
                return self._failure(action, started, f"Video prompt planning failed: {exc}")
            prompt = str(plan["prompt"])
            negative_prompt = str(plan.get("negative_prompt", ""))
            planner_model = response.model
            planner_inference = self.image_generation._inference_metrics(response.raw)

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
                async with self.image_generation.brain.resource_slot(5):
                    self._set_stage("releasing_gpu_workers")
                    await self.image_generation.stop_forge()
                    await self._release_ollama_gpu()
                    self._set_stage("generating_video")
                    video_id = new_id("video")
                    output_path = (
                        self.settings.video_output_dir / f"{video_id}.mp4"
                    ).resolve()
                    seed_value = action.payload.get("seed")
                    seed = secrets.randbelow(2**31) if seed_value is None else int(seed_value)
                    request: dict[str, Any] = {
                        "backend": backend,
                        "prompt": prompt,
                        "negative_prompt": negative_prompt,
                        "seconds": seconds,
                        "steps": self.settings.video_generation_steps,
                        "seed": seed,
                        "width": int(action.payload.get("width", 720)),
                        "height": int(action.payload.get("height", 480)),
                    }
                    if backend == "cogvideox":
                        frame_groups = max(1, min(6, round(seconds)))
                        request.update(
                            {
                                "model_dir": str(
                                    self.settings.cogvideo_model_dir.resolve()
                                ),
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
                                "source_dir": str(
                                    self.settings.framepack_source_dir.resolve()
                                ),
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
        metadata.setdefault("width", request["width"])
        metadata.setdefault("height", request["height"])
        metadata["generation_input"] = initial_metadata
        metadata["user_request"] = user_request or prompt
        metadata["planned_prompt"] = prompt
        metadata["prompt_planner_model"] = planner_model
        metadata["prompt_planner_inference"] = planner_inference
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
                "prompt_language": "english",
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

    async def _plan_user_request(self, user_request: str) -> tuple[dict[str, Any], Any]:
        adult = bool(self._sexual_terms.search(user_request))
        if adult:
            plan = self._fallback_prompt_plan(user_request)
            plan["negative_prompt"] = self._negative_prompt(
                str(plan.get("negative_prompt", "")),
                adult=True,
                request=user_request,
            )
            return plan, BrainResponse(
                content="deterministic adult media prompt",
                model="deterministic-media-compiler.v1",
                raw={},
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "/no_think\nConvert the request into a concise English CogVideoX prompt. "
                    "Preserve the exact subject, appearance, action, setting, shot type, camera "
                    "movement, and temporal motion. Do not introduce aerial city footage, drone "
                    "shots, skylines, or unrelated scenery unless requested. Legal adult nudity "
                    "or erotic content may be translated, but all depicted people must be "
                    "consenting adults age 21 or older. Never introduce minors, coercion, sexual "
                    "violence, or animals in sexual contexts. Return only JSON."
                ),
            },
            {"role": "user", "content": user_request},
        ]
        last_response: Any = None
        for attempt in range(2):
            response = await self.image_generation.brain.chat(
                messages,
                format_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "negative_prompt": {"type": "string"},
                    },
                    "required": ["prompt", "negative_prompt"],
                },
                options={
                    "temperature": 0.15,
                    "num_predict": 384,
                    "num_ctx": 2048,
                    "think": False,
                },
            )
            last_response = response
            try:
                plan = self._parse_prompt_plan(response.content)
                if not self._plan_satisfies_request(plan["prompt"], user_request):
                    raise RuntimeError(
                        "The video prompt planner changed or omitted a required constraint."
                    )
                plan["prompt"] = self._enforce_request_constraints(
                    plan["prompt"], user_request
                )
                plan["negative_prompt"] = self._negative_prompt(
                    str(plan.get("negative_prompt", "")),
                    adult=adult,
                    request=user_request,
                )
                return plan, response
            except RuntimeError:
                if attempt == 0:
                    await asyncio.sleep(0.5)
        if last_response is None:
            raise RuntimeError("The video prompt planner returned no response.")
        plan = self._fallback_prompt_plan(user_request)
        plan["negative_prompt"] = self._negative_prompt(
            str(plan.get("negative_prompt", "")),
            adult=adult,
            request=user_request,
        )
        return plan, last_response

    @staticmethod
    def _parse_prompt_plan(content: str) -> dict[str, str]:
        try:
            plan = json.loads(content)
        except ValueError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("The video prompt planner returned no JSON object.") from None
            plan = json.loads(content[start : end + 1])
        if not isinstance(plan, dict):
            raise RuntimeError("The video prompt planner returned invalid JSON.")
        prompt = str(plan.get("prompt", "")).strip()
        if len(prompt) < 3 or not any(char.isascii() and char.isalpha() for char in prompt):
            raise RuntimeError("The video prompt planner returned no usable English prompt.")
        if VideoGenerationCapability._looks_like_instruction_echo(prompt):
            raise RuntimeError(
                "The video prompt planner echoed its instructions instead of a prompt."
            )
        return {
            "prompt": prompt[:1000],
            "negative_prompt": str(plan.get("negative_prompt", ""))[:500],
        }

    @classmethod
    def _fallback_prompt_plan(cls, user_request: str) -> dict[str, str]:
        normalized = user_request.casefold()
        if re.search(r"男性|男人|男模|\bman\b|\bmale\b", normalized):
            subject = "a consenting adult man age 25 or older"
        elif re.search(r"狗|dog", normalized):
            subject = "a dog"
        elif re.search(r"貓|cat", normalized):
            subject = "a cat"
        else:
            subject = "a consenting adult woman age 25 or older"
        details = [subject]
        if re.search(r"韓國|south korean|korean", normalized):
            details.append("South Korean appearance")
        elif re.search(r"亞洲|asian|east asian", normalized):
            details.append("East Asian appearance")
        if re.search(r"網紅|influencer|internet celebrity", normalized):
            details.append("adult internet influencer")
        if cls._sexual_terms.search(user_request):
            details.extend(("fully nude consenting adult", "no clothing"))
        if re.search(r"陰部|生殖器|full frontal|explicit", normalized):
            details.append("full frontal adult nudity")
        if re.search(r"全身|full.body|head.to.toe", normalized):
            details.extend(
                (
                    "full body visible from head to toe",
                    "feet visible",
                    "uncropped long shot",
                )
            )
        if re.search(r"脫衣|脱衣|undress|remov(?:e|ing) clothes", normalized):
            details.append(
                "the adult subject removes clothing in continuous motion and ends fully nude"
            )
        if re.search(r"散步|走路|walking", normalized):
            details.extend(("walking naturally", "continuous leg and body motion"))
        if re.search(r"公園|park", normalized):
            details.append("in a green public park")
        details.extend(
            (
                "ground-level eye-level tracking shot",
                "the camera follows the subject",
                "realistic coherent motion",
                "cinematic natural light",
            )
        )
        return {"prompt": ", ".join(details), "negative_prompt": ""}

    @staticmethod
    def _negative_prompt(value: str, *, adult: bool, request: str) -> str:
        if adult:
            adult_terms = re.compile(
                r"\b(nsfw|nude|nudity|naked|erotic|porn(?:ographic)?|sexual content|"
                r"explicit(?: sexual)? content|genital(?:ia|s)?|breasts?|nipples?|"
                r"full frontal)\b|"
                r"裸體|裸露|全裸|情色|色情|陰部|生殖器|乳房裸露",
                re.IGNORECASE,
            )
            value = ", ".join(
                part.strip()
                for part in value.split(",")
                if part.strip()
                and (
                    re.search(
                        r"child|minor|underage|non[- ]?consensual|violence|bestiality|"
                        r"未成年|兒童|非自願|暴力|獸交",
                        part,
                        re.IGNORECASE,
                    )
                    or not adult_terms.search(part)
                )
            )
        required = [
            "child",
            "minor",
            "underage",
            "non-consensual",
            "sexual violence",
            "bestiality",
            "text",
            "watermark",
            "distorted anatomy",
            "scene mismatch",
        ]
        if not re.search(
            r"空拍|航拍|城市|天際線|aerial|drone|city|skyline",
            request,
            re.IGNORECASE,
        ):
            required.extend(("aerial view", "drone shot", "city skyline"))
        nude_request = re.search(
            r"裸體|裸露|全裸|一絲不掛|nude|nudity|naked|no clothing",
            request,
            re.IGNORECASE,
        )
        undressing_request = re.search(
            r"脫衣|脱衣|undress|remov(?:e|ing) clothes",
            request,
            re.IGNORECASE,
        )
        if nude_request and not undressing_request:
            required.extend(("clothes", "clothing", "underwear", "covered body"))
        return ", ".join(part for part in (value.strip(), *required) if part)[:800]

    @staticmethod
    def _looks_like_instruction_echo(prompt: str) -> bool:
        normalized = prompt.casefold()
        markers = (
            "convert the request",
            "preserve the exact subject",
            "return only json",
            "never introduce minors",
            "do not introduce aerial city footage",
        )
        return sum(marker in normalized for marker in markers) >= 2

    @classmethod
    def _plan_satisfies_request(cls, prompt: str, request: str) -> bool:
        if cls._sexual_terms.search(request) and not re.search(
            r"nude|nudity|naked|no clothing|uncovered|full frontal|adult anatomy",
            prompt,
            re.IGNORECASE,
        ):
            return False
        if re.search(r"全身|full.body|head.to.toe", request, re.IGNORECASE) and not re.search(
            r"full body|head.to.toe|feet visible|uncropped|long shot",
            prompt,
            re.IGNORECASE,
        ):
            return False
        undressing_requested = re.search(
            r"脫衣|脱衣|undress|remov(?:e|ing) clothes",
            request,
            re.IGNORECASE,
        )
        return not (
            undressing_requested
            and not re.search(
                r"undress|remov(?:e|ing) clothes|takes? off clothing",
                prompt,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _enforce_request_constraints(cls, prompt: str, request: str) -> str:
        constraints: list[str] = []
        if cls._sexual_terms.search(request):
            constraints.extend(
                (
                    "fully nude consenting adult age 21 or older",
                    "no clothing",
                )
            )
        if re.search(r"陰部|生殖器|陰毛|full frontal|genitals?|pubic hair", request, re.IGNORECASE):
            constraints.append("requested adult anatomy visible")
        if re.search(r"全身|full.body|head.to.toe", request, re.IGNORECASE):
            constraints.extend(
                (
                    "full body visible from head to toe",
                    "feet visible",
                    "uncropped long shot",
                )
            )
        if re.search(r"脫衣|脱衣|undress|remov(?:e|ing) clothes", request, re.IGNORECASE):
            constraints.append(
                "the adult subject removes clothing in continuous motion and ends fully nude"
            )
        return ", ".join((prompt.strip(), *constraints))[:1200]

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
