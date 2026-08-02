from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse
from eck.brain.mock import MockBrainProvider
from eck.capabilities.image_background import ImageBackgroundRemovalCapability
from eck.capabilities.image_generation import ImageGenerationCapability
from eck.domain.models import ActionProposal


class ImagePlanningBrain(BrainProvider):
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def health(self) -> BrainHealth:
        return BrainHealth(provider="test", available=True, model="image-planner")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        del messages, format_schema, options
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return BrainResponse(
            content=content,
            model="image-planner",
            raw={"eval_count": 17, "done_reason": "stop"},
        )


class FakeInput:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, value: bytes) -> None:
        self.writes.append(value)

    async def drain(self) -> None:
        return None


class FakeOutput:
    def __init__(self, response: bytes) -> None:
        self.response = response

    async def readline(self) -> bytes:
        return self.response


class FakeWorkerProcess:
    def __init__(self, response: bytes) -> None:
        self.stdin = FakeInput()
        self.stdout = FakeOutput(response)
        self.returncode: int | None = None
        self.killed = False

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def configure_diffusers(settings):
    configured = settings.model_copy(update={"image_backend": "diffusers"})
    configured.image_engine_python.parent.mkdir(parents=True, exist_ok=True)
    configured.image_engine_python.write_bytes(b"python")
    configured.image_engine_script.parent.mkdir(parents=True, exist_ok=True)
    configured.image_engine_script.write_text("engine", encoding="utf-8")
    configured.image_model_dir.mkdir(parents=True, exist_ok=True)
    (configured.image_model_dir / "eck-model.json").write_text(
        json.dumps(
            {
                "model_id": "stable-diffusion-v1-5",
                "revision": "main",
                "variant": "fp16",
                "format": "safetensors",
                "license": "openrail",
            }
        ),
        encoding="utf-8",
    )
    return configured


@pytest.mark.asyncio
async def test_diffusers_image_generation_success_and_failure_paths(
    settings,
    monkeypatch,
) -> None:
    configured = configure_diffusers(settings)
    capability = ImageGenerationCapability(configured, MockBrainProvider())

    async def release_vram() -> None:
        return None

    async def generate(request, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"verified-png")
        return {
            "success": True,
            "metadata": {
                "backend": "diffusers",
                "model": "stable-diffusion-v1-5",
                "seed": 42,
                "steps": request["steps"],
                "scheduler": "DPM++ Karras",
                "adetailer": False,
            },
        }

    monkeypatch.setattr(capability, "_release_ollama_vram", release_vram)
    monkeypatch.setattr(capability, "_run_diffusers", generate)

    status = capability.status()
    result = await capability.execute(
        ActionProposal(
            capability="image.generate",
            operation="generate",
            payload={"prompt": "a golden retriever in a meadow", "seed": 42},
        )
    )
    unsupported = await capability.execute(
        ActionProposal(capability="image.generate", operation="edit", payload={})
    )

    assert status["available"] and status["model"] == "stable-diffusion-v1-5"
    assert result.success and result.output["metrics"]["completed"]
    assert result.output["skill_fingerprint"].startswith("image.generate:")
    assert not unsupported.success


@pytest.mark.asyncio
async def test_user_request_planning_retries_and_records_inference(
    settings,
    monkeypatch,
) -> None:
    configured = configure_diffusers(settings)
    configured.image_model_catalog_path.parent.mkdir(parents=True, exist_ok=True)
    configured.image_model_catalog_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "alias": "chilloutmix",
                        "name": "ChilloutMix",
                        "filename": "chilloutmix.safetensors",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    brain = ImagePlanningBrain(
        [
            "not-json",
            json.dumps(
                {
                    "prompt": "adult Korean fashion model in cinematic studio light",
                    "negative_prompt": "watermark, blurry",
                    "model": "chilloutmix",
                    "use_adetailer": True,
                }
            ),
        ]
    )
    capability = ImageGenerationCapability(configured, brain)

    async def release_vram() -> None:
        return None

    async def generate(request, output_path: Path):
        assert request["model_alias"] == "chilloutmix"
        assert request["use_adetailer"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"planned-image")
        return {
            "success": True,
            "metadata": {
                "backend": "diffusers",
                "model": "stable-diffusion-v1-5",
                "seed": 7,
                "steps": request["steps"],
                "scheduler": "DPM++ Karras",
                "adetailer": False,
            },
        }

    monkeypatch.setattr(capability, "_release_ollama_vram", release_vram)
    monkeypatch.setattr(capability, "_run_diffusers", generate)
    async def skip_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr("eck.capabilities.image_generation.asyncio.sleep", skip_sleep)

    result = await capability.execute(
        ActionProposal(
            capability="image.generate",
            operation="generate",
            payload={"user_request": "Use ChilloutMix to create an adult portrait", "seed": 7},
        )
    )

    assert result.success
    assert brain.calls == 2
    assert result.output["metadata"]["prompt_planner_model"] == "image-planner"
    assert result.output["metadata"]["prompt_planner_inference"] == {"eval_count": 17}


@pytest.mark.asyncio
async def test_image_generation_rejects_unready_policy_and_invalid_engine_results(
    settings,
    monkeypatch,
) -> None:
    unavailable = ImageGenerationCapability(settings, MockBrainProvider())
    not_ready = await unavailable.execute(
        ActionProposal(capability="image.generate", operation="generate", payload={"prompt": "dog"})
    )
    assert "not ready" in not_ready.output["error"]

    configured = configure_diffusers(settings)
    capability = ImageGenerationCapability(configured, MockBrainProvider())
    empty_prompt = await capability.execute(
        ActionProposal(capability="image.generate", operation="generate", payload={"prompt": ""})
    )
    assert "descriptive" in empty_prompt.output["error"]

    with pytest.raises(ValueError, match="disabled"):
        disabled = configured.model_copy(update={"image_adult_content_enabled": False})
        ImageGenerationCapability(disabled, MockBrainProvider())._validate_request_policy(
            "adult nude portrait"
        )

    async def release_vram() -> None:
        return None

    async def missing_artifact(request, output_path: Path):
        del request, output_path
        return {"success": False, "detail": "engine failed"}

    monkeypatch.setattr(capability, "_release_ollama_vram", release_vram)
    monkeypatch.setattr(capability, "_run_diffusers", missing_artifact)
    failed = await capability.execute(
        ActionProposal(capability="image.generate", operation="generate", payload={"prompt": "dog"})
    )
    assert failed.output["error"] == "engine failed"


def test_forge_status_catalog_payload_and_model_selection(settings, monkeypatch) -> None:
    configured = settings.model_copy(update={"image_backend": "forge"})
    webui = configured.forge_root / "webui"
    (configured.forge_root / "system" / "python").mkdir(parents=True)
    (configured.forge_root / "system" / "python" / "python.exe").write_bytes(b"python")
    webui.mkdir(parents=True)
    (webui / "launch.py").write_text("launch", encoding="utf-8")
    model_dir = webui / "models" / "Stable-diffusion"
    model_dir.mkdir(parents=True)
    (model_dir / configured.forge_checkpoint).write_bytes(b"checkpoint")
    (webui / "extensions" / "adetailer").mkdir(parents=True)
    (webui / "extensions-builtin" / "sd_forge_controlnet").mkdir(parents=True)
    controlnet = webui / "models" / "ControlNet"
    controlnet.mkdir(parents=True)
    (controlnet / "control_v11p_sd15_openpose.pth").write_bytes(b"controlnet")
    configured.image_model_catalog_path.parent.mkdir(parents=True, exist_ok=True)
    configured.image_model_catalog_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "alias": "realistic_vision",
                        "name": "Realistic Vision",
                        "filename": configured.forge_checkpoint,
                    },
                    "invalid",
                ]
            }
        ),
        encoding="utf-8",
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"sd_model_checkpoint": "Realistic Vision V6"}

    monkeypatch.setattr(
        "eck.capabilities.image_generation.httpx.get",
        lambda *args, **kwargs: Response(),
    )
    capability = ImageGenerationCapability(configured, MockBrainProvider())
    status = capability.status()
    selected = capability._select_model("missing")
    payload = capability._forge_payload(
        {
            "prompt": "portrait",
            "negative_prompt": "blurry",
            "width": 512,
            "height": 512,
            "steps": 36,
            "guidance_scale": 7.5,
            "seed": None,
        },
        True,
    )

    assert status["available"] and status["worker_warm"]
    assert status["extensions"] == {"adetailer": True, "controlnet": True}
    assert selected["alias"] == "realistic_vision"
    assert payload["seed"] == -1 and "ADetailer" in payload["alwayson_scripts"]
    assert ImageGenerationCapability._forge_model_title(
        [{"filename": f"C:/models/{configured.forge_checkpoint}", "title": "RV6"}],
        configured.forge_checkpoint,
    ) == "RV6"
    with pytest.raises(RuntimeError, match="invalid model list"):
        ImageGenerationCapability._forge_model_title({}, configured.forge_checkpoint)


@pytest.mark.asyncio
async def test_background_removal_success_and_path_gates(settings, monkeypatch) -> None:
    settings.rembg_python.parent.mkdir(parents=True, exist_ok=True)
    settings.rembg_python.write_bytes(b"python")
    settings.rembg_script.parent.mkdir(parents=True, exist_ok=True)
    settings.rembg_script.write_text("worker", encoding="utf-8")
    settings.rembg_model_dir.mkdir(parents=True, exist_ok=True)
    (settings.rembg_model_dir / "birefnet-general.onnx").write_bytes(b"model")
    settings.image_output_dir.mkdir(parents=True, exist_ok=True)
    source = settings.image_output_dir / "image_source.png"
    source.write_bytes(b"source")
    capability = ImageBackgroundRemovalCapability(settings)

    async def remove(input_path: Path, output_path: Path):
        assert input_path == source.resolve()
        output_path.write_bytes(b"transparent")
        return {
            "success": True,
            "metadata": {"model": "birefnet-general", "transparent_background": True},
        }

    monkeypatch.setattr(capability, "_run_worker", remove)

    result = await capability.execute(
        ActionProposal(
            capability="image.remove_background",
            operation="remove",
            payload={"artifact_path": source.relative_to(settings.workspace_dir).as_posix()},
        )
    )
    unsupported = await capability.execute(
        ActionProposal(
            capability="image.remove_background",
            operation="crop",
            payload={},
        )
    )

    assert capability.status()["available"]
    assert result.success and result.output["skill_procedure"]["backend"] == "rembg"
    assert Path(settings.workspace_dir / result.output["artifact_path"]).is_file()
    assert not unsupported.success
    with pytest.raises(ValueError, match="generated artifact"):
        capability._input_path(settings.workspace_dir.parent / "outside.png")


@pytest.mark.asyncio
async def test_background_worker_protocol_latest_selection_and_failures(
    settings,
    monkeypatch,
) -> None:
    settings.image_output_dir.mkdir(parents=True, exist_ok=True)
    older = settings.image_output_dir / "older.png"
    latest = settings.image_output_dir / "latest.png"
    ignored = settings.image_output_dir / "image_nobg_previous.png"
    older.write_bytes(b"old")
    latest.write_bytes(b"new")
    ignored.write_bytes(b"ignored")
    os.utime(older, (1, 1))
    os.utime(latest, (2, 2))
    capability = ImageBackgroundRemovalCapability(settings)
    assert capability._input_path(None) == latest.resolve()
    with pytest.raises(FileNotFoundError, match="not found"):
        capability._input_path("generated_images/missing.png")

    process = FakeWorkerProcess(
        json.dumps({"success": True, "metadata": {"model": "birefnet-general"}}).encode()
    )
    capability._process = process  # type: ignore[assignment]
    report = await capability._run_worker(latest.resolve(), settings.image_output_dir / "out.png")
    assert report["success"] and process.stdin.writes
    await capability.close()
    assert process.returncode == 0

    invalid_process = FakeWorkerProcess(b"not-json\n")
    capability._process = invalid_process  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await capability._run_worker(latest.resolve(), settings.image_output_dir / "bad.png")

    async def failed_worker(input_path: Path, output_path: Path):
        del input_path, output_path
        return {"success": False, "detail": "rembg failed"}

    settings.rembg_python.parent.mkdir(parents=True, exist_ok=True)
    settings.rembg_python.write_bytes(b"python")
    settings.rembg_script.parent.mkdir(parents=True, exist_ok=True)
    settings.rembg_script.write_text("worker", encoding="utf-8")
    settings.rembg_model_dir.mkdir(parents=True, exist_ok=True)
    (settings.rembg_model_dir / "birefnet-general.onnx").write_bytes(b"model")
    monkeypatch.setattr(capability, "_run_worker", failed_worker)
    failed = await capability.execute(
        ActionProposal(
            capability="image.remove_background",
            operation="remove",
            payload={"artifact_path": latest.relative_to(settings.workspace_dir).as_posix()},
        )
    )
    assert failed.output["error"] == "rembg failed"
