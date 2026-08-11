from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from eck.brain.base import BrainProvider, BrainResponse
from eck.capabilities.base import Capability, CapabilityDefinition
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence


class ImageGenerationCapability(Capability):
    definition = CapabilityDefinition(
        name="image.generate",
        description=(
            "Generate original images locally with selectable SD1.5 checkpoints, Forge, "
            "ADetailer, and tamper-evident artifact evidence."
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
        r"未成年|兒童|孩童|小孩|幼女|蘿莉|正太",
        re.IGNORECASE,
    )
    _nonconsensual_terms = re.compile(
        r"\b(rape|raped|non[- ]?consensual|forced sex|sexual assault|drugged|unconscious)\b|"
        r"強姦|強奸|性侵|非自願|迷姦|昏迷性交|偷拍性愛",
        re.IGNORECASE,
    )
    _bestiality_terms = re.compile(
        r"\b(bestiality|zoophilia|animal sex)\b|獸交|人獸性交",
        re.IGNORECASE,
    )

    def __init__(self, settings: Settings, brain: BrainProvider) -> None:
        self.settings = settings
        self.brain = brain
        self._engine_process: asyncio.subprocess.Process | None = None
        self._engine_lock = asyncio.Lock()
        self._forge_lock = asyncio.Lock()
        self._forge_api_ready = False
        self._forge_runtime_checked_at = 0.0
        self._forge_runtime_cached = (False, settings.forge_checkpoint)
        self._forge_idle_task: asyncio.Task[None] | None = None
        self._activity_stage = "idle"
        self._activity_started_at: str | None = None

    def status(self) -> dict[str, Any]:
        if self.settings.image_backend == "forge":
            return self._forge_status()
        return self._diffusers_status()

    def _forge_status(self) -> dict[str, Any]:
        webui = self.settings.forge_root / "webui"
        model_dir = webui / "models" / "Stable-diffusion"
        models = self._catalog_models()
        api_ready, active_checkpoint = self._forge_runtime_status()
        checks = {
            "python": (self.settings.forge_root / "system" / "python" / "python.exe").is_file(),
            "engine": (webui / "launch.py").is_file(),
            "catalog": self.settings.image_model_catalog_path.is_file(),
            "checkpoint": (model_dir / self.settings.forge_checkpoint).is_file(),
            "adetailer": (webui / "extensions" / "adetailer").is_dir(),
            "controlnet": (
                webui / "extensions-builtin" / "sd_forge_controlnet"
            ).is_dir()
            and (
                webui
                / "models"
                / "ControlNet"
                / "control_v11p_sd15_openpose.pth"
            ).is_file(),
        }
        installed_ready = all(checks.values())
        return {
            "enabled": self.settings.image_generation_enabled,
            "available": self.settings.image_generation_enabled and installed_ready,
            "installed_ready": installed_ready,
            "runtime_ready": api_ready,
            "runtime_state": (
                "warm"
                if api_ready
                else "available_on_demand" if installed_ready else "unavailable"
            ),
            "backend": "forge",
            "local_only": True,
            "paid_api": False,
            "api_url": self.settings.forge_base_url,
            "worker_warm": api_ready,
            "checks": checks,
            "model": active_checkpoint,
            "configured_model": self.settings.forge_checkpoint,
            "models": [
                {
                    **model,
                    "installed": (model_dir / str(model.get("filename", ""))).is_file(),
                }
                for model in models
            ],
            "extensions": {
                "adetailer": checks["adetailer"],
                "controlnet": checks["controlnet"],
            },
            "content_policy": {
                "legal_adult_content": self.settings.image_adult_content_enabled,
                "minor_sexual_content": False,
                "nonconsensual_sexual_content": False,
                "bestiality": False,
            },
            "quality": {
                "width": 512,
                "height": 512,
                "steps": self.settings.image_generation_steps,
                "guidance_scale": self.settings.image_generation_guidance_scale,
                "scheduler": "DPM++ 2M Karras",
                "adetailer": self.settings.image_adetailer_enabled,
            },
            "activity": self._activity_status(),
        }

    def _forge_runtime_status(self) -> tuple[bool, str]:
        now = time.monotonic()
        if (
            now - self._forge_runtime_checked_at
            < self.settings.brain_health_cache_seconds
        ):
            return self._forge_runtime_cached
        try:
            response = httpx.get(
                f"{self.settings.forge_base_url.rstrip('/')}/sdapi/v1/options",
                timeout=0.5,
            )
            response.raise_for_status()
            checkpoint = str(response.json().get("sd_model_checkpoint", "")).strip()
            self._forge_api_ready = True
            result = (True, checkpoint or self.settings.forge_checkpoint)
        except (httpx.HTTPError, ValueError):
            result = (self._forge_api_ready, self.settings.forge_checkpoint)
        self._forge_runtime_checked_at = now
        self._forge_runtime_cached = result
        return result

    def _diffusers_status(self) -> dict[str, Any]:
        manifest_path = self.settings.image_model_dir / "eck-model.json"
        manifest: dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
        checks = {
            "python": self.settings.image_engine_python.is_file(),
            "engine": self.settings.image_engine_script.is_file(),
            "model": manifest_path.is_file(),
        }
        return {
            "enabled": self.settings.image_generation_enabled,
            "available": self.settings.image_generation_enabled and all(checks.values()),
            "backend": "diffusers",
            "local_only": True,
            "paid_api": False,
            "worker_warm": bool(
                self._engine_process and self._engine_process.returncode is None
            ),
            "checks": checks,
            "model": manifest.get("model_id"),
            "revision": manifest.get("revision"),
            "variant": manifest.get("variant"),
            "format": manifest.get("format"),
            "license": manifest.get("license"),
            "quality": {
                "width": 512,
                "height": 512,
                "steps": self.settings.image_generation_steps,
                "guidance_scale": self.settings.image_generation_guidance_scale,
                "scheduler": "DPM++ Karras",
                "adetailer": False,
            },
            "activity": self._activity_status(),
        }

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        status = self.status()
        if action.operation != "generate":
            return self._failure(action, started, "Image generation supports only 'generate'.")
        if not status["available"]:
            return self._failure(action, started, "The local image engine is not ready.", status)

        planner_model: str | None = None
        planner_inference: dict[str, Any] = {}
        model_alias = str(action.payload.get("model", "")).strip()
        use_adetailer = bool(action.payload.get("use_adetailer", False))
        user_request = ""
        try:
            self._set_activity("planning_prompt")
            user_request = str(action.payload.get("user_request", "")).strip()
            adult_request = self._validate_request_policy(user_request)
            if user_request:
                requested_model_alias = self._requested_model_alias(user_request)
                plan, plan_response = await self._plan_user_request(user_request)
                planned_prompt = self._enforce_request_constraints(
                    str(plan["prompt"]), user_request
                )
                if not requested_model_alias:
                    planned_prompt = self._strip_model_selection_artifacts(planned_prompt)
                prompt = self._quality_prompt(planned_prompt, adult=adult_request)
                negative_prompt = self._negative_prompt(
                    str(plan.get("negative_prompt", "")),
                    adult=adult_request,
                    request=user_request,
                )
                model_alias = (
                    model_alias
                    or requested_model_alias
                    or self._recommended_model_alias(user_request)
                    or str(plan.get("model", ""))
                )
                use_adetailer = bool(
                    action.payload.get(
                        "use_adetailer",
                        plan.get("use_adetailer", self._prompt_depicts_people(prompt)),
                    )
                ) and self._prompt_depicts_people(user_request)
                planner_model = plan_response.model
                planner_inference = self._inference_metrics(plan_response.raw)
            else:
                prompt = str(action.payload.get("prompt", "")).strip()[:1200]
                if len(prompt) < 3:
                    self._set_activity("idle")
                    return self._failure(
                        action, started, "A descriptive image prompt is required."
                    )
                adult_request = self._validate_request_policy(prompt)
                prompt = self._quality_prompt(prompt, adult=adult_request)
                negative_prompt = self._negative_prompt(
                    str(action.payload.get("negative_prompt", "")),
                    adult=adult_request,
                    request=prompt,
                )
                use_adetailer = bool(
                    action.payload.get(
                        "use_adetailer", self._prompt_depicts_people(prompt)
                    )
                )
        except (RuntimeError, httpx.HTTPError) as exc:
            if not user_request:
                self._set_activity("idle")
                return self._failure(action, started, f"Image prompt planning failed: {exc}")
            plan = self._fallback_prompt_plan(user_request)
            direct_prompt = f"{user_request}, {plan['prompt']}"
            prompt = self._quality_prompt(direct_prompt, adult=adult_request)
            negative_prompt = self._negative_prompt(
                str(plan.get("negative_prompt", "")),
                adult=adult_request,
                request=user_request,
            )
            model_alias = (
                model_alias
                or self._requested_model_alias(user_request)
                or self._recommended_model_alias(user_request)
                or str(plan.get("model", ""))
            )
            use_adetailer = bool(
                action.payload.get(
                    "use_adetailer",
                    plan.get("use_adetailer", self._prompt_depicts_people(prompt)),
                )
            ) and self._prompt_depicts_people(user_request)
            planner_model = "deterministic-direct-prompt-fallback.v1"
            planner_inference = {
                "fallback": True,
                "reason": str(exc)[:500],
            }
        except ValueError as exc:
            self._set_activity("idle")
            return self._failure(action, started, f"Image prompt planning failed: {exc}")
        if len(prompt) < 3:
            self._set_activity("idle")
            return self._failure(action, started, "A descriptive image prompt is required.")

        width = self._dimension(action.payload.get("width", 512))
        height = self._dimension(action.payload.get("height", 512))
        steps = min(
            50,
            max(
                self.settings.image_generation_steps,
                int(action.payload.get("steps", self.settings.image_generation_steps)),
            ),
        )
        guidance_scale = min(
            12.0,
            max(
                1.0,
                float(
                    action.payload.get(
                        "guidance_scale",
                        self.settings.image_generation_guidance_scale,
                    )
                ),
            ),
        )
        selected_model = self._select_model(model_alias)
        image_id = new_id("image")
        output_path = (self.settings.image_output_dir / f"{image_id}.png").resolve()
        request = {
            "model_dir": str(self.settings.image_model_dir.resolve()),
            "checkpoint": selected_model.get("filename"),
            "model_alias": selected_model.get("alias"),
            "model_name": selected_model.get("name"),
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "guidance_scale": guidance_scale,
            "seed": action.payload.get("seed"),
            "use_adetailer": self.settings.image_adetailer_enabled and use_adetailer,
            "adult_request": adult_request,
        }
        try:
            async with self.brain.resource_slot(5):
                self._set_activity("releasing_brain_vram")
                await asyncio.wait_for(self._release_ollama_vram(), timeout=45)
                self._set_activity("generating_image")
                if self.settings.image_backend == "forge":
                    report = await self._run_forge(request, output_path)
                else:
                    report = await self._run_diffusers(request, output_path)
                self._set_activity("verifying_artifact")
        except TimeoutError:
            return self._failure(action, started, "The local image pipeline timed out.")
        except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
            return self._failure(action, started, str(exc))
        finally:
            self._set_activity("idle")

        if not report.get("success") or not output_path.is_file():
            detail = str(report.get("detail") or "Image generation did not produce an artifact.")
            return self._failure(action, started, detail, report)

        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        metadata = report.get("metadata", {})
        if not isinstance(metadata, dict):
            return self._failure(action, started, "The image engine returned invalid metadata.")
        actual_dimensions = self._png_dimensions(output_path)
        if actual_dimensions is not None:
            metadata["requested_width"] = width
            metadata["requested_height"] = height
            metadata["width"], metadata["height"] = actual_dimensions
        metadata["prompt_planner_model"] = planner_model
        metadata["prompt_planner_inference"] = planner_inference
        relative_path = output_path.relative_to(self.settings.workspace_dir.resolve()).as_posix()
        finished = utc_now()
        total_elapsed = round((finished - started).total_seconds(), 3)
        metadata["total_elapsed_seconds"] = total_elapsed
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
            "skill_fingerprint": self._skill_fingerprint(metadata),
            "skill_name": self._skill_name(metadata),
            "skill_procedure": {
                "backend": metadata.get("backend", self.settings.image_backend),
                "checkpoint": metadata.get("checkpoint") or metadata.get("model"),
                "sampler": metadata.get("sampler"),
                "scheduler": metadata.get("scheduler"),
                "steps": metadata.get("steps"),
                "adetailer": bool(metadata.get("adetailer")),
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
                    claim="The local image engine wrote and hashed a PNG artifact.",
                    payload={
                        "artifact": output_path.name,
                        "bytes": output_path.stat().st_size,
                        "sha256": digest,
                        "seed": metadata.get("seed"),
                        "backend": metadata.get("backend"),
                        "checkpoint": metadata.get("checkpoint"),
                    },
                ),
            ),
            reversible=True,
            cost_units=max(1.0, total_elapsed),
            started_at=started,
            finished_at=finished,
        )

    @staticmethod
    def _skill_fingerprint(metadata: dict[str, Any]) -> str:
        checkpoint = str(metadata.get("checkpoint") or metadata.get("model") or "sd15")
        normalized = re.sub(r"[^a-z0-9]+", "-", checkpoint.casefold()).strip("-")
        detailer = "adetailer" if metadata.get("adetailer") else "base"
        return f"image.generate:{normalized[:80] or 'sd15'}:{detailer}"

    @staticmethod
    def _skill_name(metadata: dict[str, Any]) -> str:
        checkpoint = str(metadata.get("checkpoint") or metadata.get("model") or "SD1.5")
        suffix = " + ADetailer" if metadata.get("adetailer") else ""
        return f"本機圖像生成：{checkpoint}{suffix}"

    async def _plan_user_request(
        self, user_request: str
    ) -> tuple[dict[str, Any], BrainResponse]:
        if self._sexual_terms.search(user_request):
            return self._fallback_prompt_plan(user_request), BrainResponse(
                content="deterministic adult media prompt",
                model="deterministic-media-compiler.v1",
                raw={},
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "/no_think\nConvert the user's request into a concise English Stable "
                    "Diffusion 1.5 prompt. Preserve subject, setting, composition, and style. "
                    "For full-body requests, preserve a head-to-toe uncropped long shot instead "
                    "of converting it into a portrait. Otherwise keep the complete face and head "
                    "in frame. Legal "
                    "adult nudity or erotic requests may be translated, but every depicted "
                    "person must be an adult age 21 or older. Never introduce minors, coercion, "
                    "or sexual violence. Select realistic_vision for balanced realism, "
                    "chilloutmix for East Asian editorial portraits, or cyberrealistic for "
                    "extreme realism, materials, and outdoor light. Enable ADetailer when a "
                    "human face is visible. Do not put checkpoint names, ADetailer, or model "
                    "selection explanations inside the image prompt. Return only the requested "
                    "JSON fields."
                ),
            },
            {"role": "user", "content": user_request},
        ]
        last_detail = "no response"
        last_response: BrainResponse | None = None
        for attempt in range(2):
            response = await self.brain.chat(
                messages,
                format_schema=self._prompt_schema(),
                options={
                    "temperature": 0.2,
                    "num_predict": 512,
                    "num_ctx": 2048,
                    "think": False,
                },
            )
            last_response = response
            try:
                plan = self._parse_plan(response.content)
                if not self._plan_satisfies_request(str(plan["prompt"]), user_request):
                    raise RuntimeError(
                        "The prompt planner changed or omitted a required user constraint."
                    )
                return plan, response
            except RuntimeError as exc:
                raw_message = response.raw.get("message", {})
                thinking = (
                    raw_message.get("thinking", "")
                    if isinstance(raw_message, dict)
                    else ""
                )
                last_detail = (
                    f"{exc} content_chars={len(response.content)} "
                    f"thinking_chars={len(str(thinking))} "
                    f"done_reason={response.raw.get('done_reason')}"
                )
                if attempt == 0:
                    await asyncio.sleep(0.75)
        if last_response is None:
            raise RuntimeError(last_detail)
        return self._fallback_prompt_plan(user_request), last_response

    @classmethod
    def _fallback_prompt_plan(cls, user_request: str) -> dict[str, Any]:
        normalized = user_request.casefold()
        adult = bool(cls._sexual_terms.search(user_request))
        if re.search(r"男性|男人|男模|\bman\b|\bmale\b", normalized):
            subject = "a consenting adult man age 25 or older"
        elif re.search(r"狗|dog", normalized):
            subject = "a dog"
        elif re.search(r"貓|cat", normalized):
            subject = "a cat"
        else:
            subject = "a consenting adult woman age 25 or older"
        details: list[str] = [subject]
        if re.search(r"韓國|south korean|korean", normalized):
            details.append("South Korean appearance")
        elif re.search(r"日本|japanese", normalized):
            details.append("Japanese appearance")
        elif re.search(r"亞洲|asian|east asian", normalized):
            details.append("East Asian appearance")
        if re.search(r"網紅|influencer|internet celebrity", normalized):
            details.append("adult internet influencer")
        if re.search(r"白皙|pale skin|fair skin", normalized):
            details.append("fair skin")
        if adult:
            details.extend(("fully nude adult", "no clothing", "uncovered body"))
        if re.search(r"陰部|生殖器|陰毛|full frontal|genitals?|pubic hair|explicit", normalized):
            details.append("full frontal adult nudity")
        if re.search(r"全身|full.body|head.to.toe", normalized):
            details.extend(
                (
                    "full body visible from head to toe",
                    "feet visible",
                    "uncropped long shot",
                )
            )
        if re.search(r"9[:：]16", normalized):
            details.append("vertical 9:16 composition")
        elif re.search(r"16[:：]9", normalized):
            details.append("wide 16:9 composition")
        if re.search(r"散步|走路|walking", normalized):
            details.append("walking naturally")
        if re.search(r"公園|park", normalized):
            details.append("in a public park")
        if re.search(r"室內|studio|攝影棚", normalized):
            details.append("in a professional photography studio")
        details.extend(("realistic photography", "natural anatomy", "coherent composition"))
        return {
            "prompt": ", ".join(details),
            "negative_prompt": "low quality, blurry, distorted anatomy, text, watermark",
            "model": (
                "chilloutmix"
                if re.search(r"亞洲|韓國|日本|asian|korean|japanese", user_request, re.IGNORECASE)
                else "realistic_vision"
            ),
            "use_adetailer": "woman" in subject or "man" in subject,
        }

    @staticmethod
    def _parse_plan(content: str) -> dict[str, Any]:
        try:
            plan = json.loads(content)
        except ValueError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("The prompt planner returned no JSON object.") from None
            plan = json.loads(content[start : end + 1])
        if not isinstance(plan, dict):
            raise RuntimeError("The prompt planner returned an invalid JSON object.")
        prompt = str(plan.get("prompt", "")).strip()
        contains_english = any(
            character.isascii() and character.isalpha() for character in prompt
        )
        if len(prompt) < 3 or not contains_english:
            raise RuntimeError("The prompt planner returned no usable English prompt.")
        if ImageGenerationCapability._looks_like_instruction_echo(prompt):
            raise RuntimeError("The prompt planner echoed its instructions instead of a prompt.")
        model = str(plan.get("model", "realistic_vision"))
        if model not in {"realistic_vision", "chilloutmix", "cyberrealistic"}:
            model = "realistic_vision"
        plan["prompt"] = prompt[:900]
        plan["model"] = model
        plan["use_adetailer"] = bool(plan.get("use_adetailer", False))
        return plan

    @staticmethod
    def _quality_prompt(prompt: str, *, adult: bool = False) -> str:
        adult_scope = ", all depicted people are consenting adults age 21 or older" if adult else ""
        return (
            f"{prompt.strip()}{adult_scope}, high quality, highly detailed, professional "
            "composition, natural lighting, sharp focus"
        )[:1200]

    @staticmethod
    def _negative_prompt(
        value: str,
        *,
        adult: bool = False,
        request: str = "",
    ) -> str:
        if adult:
            adult_suppression = re.compile(
                r"\b(nsfw|nude|nudity|naked|erotic|porn(?:ographic)?|sexual content|"
                r"explicit(?: sexual)? content|genital(?:ia|s)?|breasts?|nipples?|"
                r"full frontal)\b|"
                r"裸體|裸露|全裸|情色|色情|成人內容|陰部|生殖器|乳房裸露",
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
                    or not adult_suppression.search(part)
                )
            )
        required = (
            "child, children, minor, underage, non-consensual, sexual violence, bestiality, "
            "low quality, worst quality, blurry, distorted, deformed, duplicate, bad anatomy, "
            "extra limbs, extra fingers, poorly drawn hands, poorly drawn face, text, letters, "
            "caption, signature, watermark, logo, typography"
        )
        framing = ""
        if re.search(r"全身|full.body|head.to.toe", request, re.IGNORECASE):
            framing = "cropped, close-up, medium shot, partial body, feet out of frame, "
        clothing = ""
        if re.search(
            r"裸體|裸露|全裸|一絲不掛|nude|nudity|naked|no clothing",
            request,
            re.IGNORECASE,
        ):
            clothing = "clothes, clothing, dress, underwear, lingerie, bikini, covered body, "
        return f"{value.strip()}, {framing}{clothing}{required}".strip(", ")[:1200]

    @staticmethod
    def _looks_like_instruction_echo(prompt: str) -> bool:
        normalized = prompt.casefold()
        markers = (
            "convert the user's request",
            "convert the user request",
            "return only the requested json",
            "return only json",
            "select realistic_vision",
            "never introduce minors",
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
        return not (
            re.search(r"韓國|korean", request, re.IGNORECASE)
            and not re.search(r"korean|east asian", prompt, re.IGNORECASE)
        )

    @staticmethod
    def _recommended_model_alias(request: str) -> str:
        if re.search(r"亞洲|韓國|日本|台灣|asian|korean|japanese", request, re.IGNORECASE):
            return "chilloutmix"
        if re.search(r"戶外|材質|配件|outdoor|material|accessor", request, re.IGNORECASE):
            return "cyberrealistic"
        return ""

    @classmethod
    def _enforce_request_constraints(cls, prompt: str, request: str) -> str:
        constraints: list[str] = []
        if cls._sexual_terms.search(request):
            constraints.extend(
                (
                    "fully nude consenting adult age 21 or older",
                    "no clothing",
                    "uncovered adult body",
                )
            )
        if re.search(r"陰部|生殖器|陰毛|full frontal|genitals?|pubic hair", request, re.IGNORECASE):
            constraints.append("requested adult anatomy clearly visible")
        if re.search(r"全身|full.body|head.to.toe", request, re.IGNORECASE):
            constraints.extend(
                (
                    "full body visible from head to toe",
                    "feet visible",
                    "uncropped long shot",
                )
            )
        if re.search(r"9[:：]16", request):
            constraints.append("vertical 9:16 composition")
        elif re.search(r"16[:：]9", request):
            constraints.append("wide 16:9 composition")
        return ", ".join((prompt.strip(), *constraints))[:1100]

    @staticmethod
    def _requested_model_alias(request: str) -> str:
        normalized = request.casefold().replace("-", "_").replace(" ", "_")
        if "cyberrealistic" in normalized or "cyber_realistic" in normalized:
            return "cyberrealistic"
        if "chilloutmix" in normalized or "chillout_mix" in normalized:
            return "chilloutmix"
        if "realisticvision" in normalized or "realistic_vision" in normalized:
            return "realistic_vision"
        return ""

    @staticmethod
    def _strip_model_selection_artifacts(prompt: str) -> str:
        selection_terms = (
            "realistic vision",
            "realistic_vision",
            "chilloutmix",
            "chillout mix",
            "cyberrealistic",
            "cyber realistic",
            "adetailer",
        )
        clauses = [part.strip() for part in prompt.split(",")]
        filtered = [
            clause
            for clause in clauses
            if clause and not any(term in clause.casefold() for term in selection_terms)
        ]
        return ", ".join(filtered) or prompt.strip()

    @staticmethod
    def _prompt_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "negative_prompt": {"type": "string"},
                "model": {
                    "type": "string",
                    "enum": ["realistic_vision", "chilloutmix", "cyberrealistic"],
                },
                "use_adetailer": {"type": "boolean"},
            },
            "required": ["prompt", "negative_prompt", "model", "use_adetailer"],
        }

    def _validate_request_policy(self, request: str) -> bool:
        if not request:
            return False
        sexual = bool(self._sexual_terms.search(request))
        if sexual and self._minor_terms.search(request):
            raise ValueError("Sexual content involving minors is permanently prohibited.")
        if self._nonconsensual_terms.search(request):
            raise ValueError("Non-consensual sexual content is permanently prohibited.")
        if self._bestiality_terms.search(request):
            raise ValueError("Sexual content involving animals is permanently prohibited.")
        if sexual and not self.settings.image_adult_content_enabled:
            raise ValueError("Legal adult image generation is disabled by configuration.")
        return sexual

    @staticmethod
    def _prompt_depicts_people(prompt: str) -> bool:
        return bool(
            re.search(
                r"\b(person|people|woman|women|man|men|girl|boy|face|portrait|model|human)\b|"
                r"人物|人像|女性|女人|男人|男性|女孩|男孩|臉部|面孔|模特兒",
                prompt,
                re.IGNORECASE,
            )
        )

    def _catalog_models(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(
                self.settings.image_model_catalog_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return []
        items = payload.get("models", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def _select_model(self, alias: str) -> dict[str, Any]:
        models = self._catalog_models()
        requested = alias.strip().lower()
        for model in models:
            if requested and requested in {
                str(model.get("alias", "")).lower(),
                str(model.get("filename", "")).lower(),
            }:
                return model
        for model in models:
            if str(model.get("filename", "")) == self.settings.forge_checkpoint:
                return model
        if models:
            return models[0]
        return {
            "alias": "stable_diffusion_15",
            "name": "Stable Diffusion 1.5",
            "filename": self.settings.forge_checkpoint,
        }

    async def _run_forge(
        self, request: dict[str, Any], output_path: Path
    ) -> dict[str, Any]:
        self._cancel_forge_idle_shutdown()
        try:
            await asyncio.wait_for(self._forge_lock.acquire(), timeout=10)
        except TimeoutError as exc:
            raise RuntimeError("Another Forge request held the generation lock too long.") from exc
        try:
            self._set_activity("starting_forge")
            await self._ensure_forge_api()
            self._set_activity("generating_image")
            timeout = httpx.Timeout(self.settings.image_generation_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                base_url = self.settings.forge_base_url.rstrip("/")
                models_response = await client.get(f"{base_url}/sdapi/v1/sd-models")
                models_response.raise_for_status()
                model_title = self._forge_model_title(
                    models_response.json(), str(request["checkpoint"])
                )
                options_response = await client.get(f"{base_url}/sdapi/v1/options")
                options_response.raise_for_status()
                current_model = str(options_response.json().get("sd_model_checkpoint", ""))
                if not current_model or (
                    model_title not in current_model and current_model not in model_title
                ):
                    switch_response = await client.post(
                        f"{base_url}/sdapi/v1/options",
                        json={"sd_model_checkpoint": model_title},
                    )
                    switch_response.raise_for_status()
                use_adetailer = bool(request.get("use_adetailer"))
                if use_adetailer:
                    scripts_response = await client.get(f"{base_url}/sdapi/v1/scripts")
                    scripts_response.raise_for_status()
                    scripts = scripts_response.json().get("txt2img", [])
                    if not any(str(item).lower() == "adetailer" for item in scripts):
                        raise RuntimeError("ADetailer is installed but not loaded by Forge.")
                payload = self._forge_payload(request, use_adetailer)
                started = time.perf_counter()
                response = await client.post(f"{base_url}/sdapi/v1/txt2img", json=payload)
                response.raise_for_status()
                data = response.json()
        finally:
            self._forge_lock.release()
            self._schedule_forge_idle_shutdown()
        images = data.get("images", [])
        if not images:
            raise RuntimeError("Forge returned no generated image.")
        encoded = str(images[0])
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(encoded, validate=True))
        info = data.get("info", {})
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except ValueError:
                info = {}
        if not isinstance(info, dict):
            info = {}
        metadata = {
            "backend": "forge",
            "model": request.get("model_name"),
            "model_alias": request.get("model_alias"),
            "checkpoint": request.get("checkpoint"),
            "checkpoint_title": model_title,
            "sampler": "DPM++ 2M",
            "scheduler": "Karras",
            "prompt": request["prompt"],
            "negative_prompt": request["negative_prompt"],
            "seed": info.get("seed", request.get("seed")),
            "steps": request["steps"],
            "guidance_scale": request["guidance_scale"],
            "width": request["width"],
            "height": request["height"],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "adetailer": use_adetailer,
            "adetailer_model": self.settings.image_adetailer_model if use_adetailer else None,
            "adult_request": bool(request.get("adult_request")),
            "local_only": True,
            "paid_api": False,
        }
        output_path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"success": True, "artifact": str(output_path), "metadata": metadata}

    def _forge_payload(
        self, request: dict[str, Any], use_adetailer: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": request["prompt"],
            "negative_prompt": request["negative_prompt"],
            "width": request["width"],
            "height": request["height"],
            "steps": request["steps"],
            "cfg_scale": request["guidance_scale"],
            "sampler_name": "DPM++ 2M",
            "scheduler": "Karras",
            "seed": int(request["seed"]) if request.get("seed") is not None else -1,
            "batch_size": 1,
            "n_iter": 1,
            "send_images": True,
            "save_images": False,
        }
        if use_adetailer:
            payload["alwayson_scripts"] = {
                "ADetailer": {
                    "args": [
                        True,
                        False,
                        {
                            "ad_model": self.settings.image_adetailer_model,
                            "ad_prompt": "[PROMPT], detailed face, natural skin texture",
                            "ad_negative_prompt": (
                                "deformed face, asymmetrical eyes, poorly drawn face"
                            ),
                            "ad_confidence": 0.3,
                            "ad_mask_k": 1,
                            "ad_mask_blur": 4,
                            "ad_dilate_erode": 4,
                            "ad_denoising_strength": 0.35,
                            "ad_inpaint_only_masked": True,
                            "ad_inpaint_only_masked_padding": 32,
                        },
                    ]
                }
            }
        return payload

    @staticmethod
    def _forge_model_title(models: Any, filename: str) -> str:
        if not isinstance(models, list):
            raise RuntimeError("Forge returned an invalid model list.")
        for item in models:
            if not isinstance(item, dict):
                continue
            item_filename = Path(str(item.get("filename", ""))).name
            title = str(item.get("title") or item.get("model_name") or "")
            if item_filename.lower() == filename.lower():
                return title or item_filename
        raise RuntimeError(f"Forge did not discover checkpoint: {filename}")

    async def _ensure_forge_api(self) -> None:
        if await self._forge_health():
            self._forge_api_ready = True
            self._forge_runtime_checked_at = 0.0
            return
        if not self.settings.forge_auto_start:
            raise RuntimeError("Forge is offline and automatic startup is disabled.")
        parsed = urlparse(self.settings.forge_base_url)
        environment = os.environ.copy()
        environment.update(
            {
                "ECK_FORGE_ROOT": str(self.settings.forge_root.resolve()),
                "ECK_FORGE_PORT": str(parsed.port or 7861),
                "ECK_FORGE_CHECKPOINT": self.settings.forge_checkpoint,
            }
        )
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.settings.forge_start_script.resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        launcher_returned = True
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60,
            )
        except TimeoutError:
            launcher_returned = False
            process.kill()
            await process.wait()
            stdout, stderr = b"", b""
        if launcher_returned and process.returncode != 0:
            detail = stderr.decode(errors="replace").strip() or stdout.decode(
                errors="replace"
            ).strip()
            raise RuntimeError(f"Forge startup failed: {detail[-2000:]}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.forge_startup_timeout_seconds
        while loop.time() < deadline:
            if await self._forge_health():
                self._forge_api_ready = True
                self._forge_runtime_checked_at = 0.0
                return
            await asyncio.sleep(2)
        raise RuntimeError("Forge API did not become ready before its startup deadline.")

    async def _forge_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(
                    f"{self.settings.forge_base_url.rstrip('/')}/sdapi/v1/options"
                )
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _run_diffusers(
        self, request: dict[str, Any], output_path: Path
    ) -> dict[str, Any]:
        request_path = output_path.with_suffix(".request.json")
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            return await self._run_engine(request_path, output_path)
        finally:
            request_path.unlink(missing_ok=True)

    @staticmethod
    def _inference_metrics(raw: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        )
        return {key: raw[key] for key in keys if key in raw}

    async def _release_ollama_vram(self) -> None:
        if self.settings.brain_provider != "ollama" or not self.settings.ollama_model:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": 0,
                },
            )
            response.raise_for_status()

    async def _run_engine(self, request_path: Path, output_path: Path) -> dict[str, Any]:
        async with self._engine_lock:
            process = await self._ensure_engine_process()
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("The local image engine pipes are unavailable.")
            command = json.dumps(
                {"request": str(request_path), "output": str(output_path)},
                ensure_ascii=False,
            )
            process.stdin.write(f"{command}\n".encode())
            await process.stdin.drain()
            try:
                response = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=self.settings.image_generation_timeout_seconds,
                )
            except TimeoutError as exc:
                await self._stop_engine_process()
                raise RuntimeError("The local image engine timed out.") from exc
            if not response:
                await self._stop_engine_process()
                raise RuntimeError("The local image engine stopped without a result.")
        try:
            report = json.loads(response.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise RuntimeError("The local image engine returned invalid JSON.") from exc
        if not isinstance(report, dict):
            raise RuntimeError("The local image engine returned an invalid result object.")
        return report

    async def close(self) -> None:
        self._cancel_forge_idle_shutdown()
        async with self._engine_lock:
            await self._stop_engine_process(graceful=True)
        with suppress(OSError, RuntimeError, TimeoutError):
            await self.stop_forge()

    async def stop_forge(self) -> None:
        idle_task = self._forge_idle_task
        if idle_task and idle_task is not asyncio.current_task():
            idle_task.cancel()
            self._forge_idle_task = None
        if self.settings.image_backend != "forge" or not await self._forge_health():
            return
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.settings.forge_stop_script.resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip() or stdout.decode(
                errors="replace"
            ).strip()
            raise RuntimeError(f"Forge shutdown failed: {detail[-1000:]}")
        self._forge_api_ready = False
        self._forge_runtime_checked_at = time.monotonic()
        self._forge_runtime_cached = (False, self.settings.forge_checkpoint)

    def _cancel_forge_idle_shutdown(self) -> None:
        task = self._forge_idle_task
        if task and not task.done():
            task.cancel()
        self._forge_idle_task = None

    def _schedule_forge_idle_shutdown(self) -> None:
        self._cancel_forge_idle_shutdown()
        if self.settings.forge_idle_shutdown_seconds <= 0:
            return
        self._forge_idle_task = asyncio.create_task(
            self._stop_forge_after_idle(),
            name="eck-forge-idle-shutdown",
        )

    async def _stop_forge_after_idle(self) -> None:
        try:
            await asyncio.sleep(self.settings.forge_idle_shutdown_seconds)
            await self.stop_forge()
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TimeoutError):
            return
        finally:
            if self._forge_idle_task is asyncio.current_task():
                self._forge_idle_task = None

    def _set_activity(self, stage: str) -> None:
        if stage != self._activity_stage:
            self._activity_stage = stage
            self._activity_started_at = (
                None if stage == "idle" else utc_now().isoformat()
            )

    def _activity_status(self) -> dict[str, Any]:
        return {
            "stage": self._activity_stage,
            "started_at": self._activity_started_at,
            "busy": self._activity_stage != "idle",
        }

    async def _ensure_engine_process(self) -> asyncio.subprocess.Process:
        if self._engine_process and self._engine_process.returncode is None:
            return self._engine_process
        environment = os.environ.copy()
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "DIFFUSERS_VERBOSITY": "error",
            }
        )
        self._engine_process = await asyncio.create_subprocess_exec(
            str(self.settings.image_engine_python.resolve()),
            str(self.settings.image_engine_script.resolve()),
            "--serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
        return self._engine_process

    async def _stop_engine_process(self, *, graceful: bool = False) -> None:
        process = self._engine_process
        self._engine_process = None
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
        self,
        action: ActionProposal,
        started: Any,
        detail: str,
        extra: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        output = {"error": detail, "metrics": {"completed": False}}
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
                    claim="The local image engine did not produce a verified artifact.",
                    payload={"detail": detail},
                ),
            ),
            reversible=True,
            cost_units=0,
            started_at=started,
            finished_at=utc_now(),
        )

    @staticmethod
    def _dimension(value: object) -> int:
        dimension = min(1536, max(256, int(str(value))))
        return dimension - dimension % 8

    @staticmethod
    def _png_dimensions(path: Path) -> tuple[int, int] | None:
        with path.open("rb") as image_file:
            header = image_file.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return int.from_bytes(header[16:20], "big"), int.from_bytes(
            header[20:24], "big"
        )
