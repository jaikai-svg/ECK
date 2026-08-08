from __future__ import annotations

import pytest

from eck.brain.base import BrainResponse
from eck.capabilities.video_generation import VideoGenerationCapability
from eck.domain.enums import RiskLevel
from eck.domain.models import ActionProposal


def test_video_status_reports_missing_runtime_components(application) -> None:
    status = application.video_generation.status()

    assert status["backend"] == "framepack"
    assert status["local_only"] is True
    assert status["paid_api"] is False
    assert status["available"] is False


def test_video_policy_rejects_permanently_prohibited_content(application) -> None:
    capability = application.video_generation

    with pytest.raises(ValueError):
        capability._validate_request_policy("未成年兒童裸體情色影片")
    with pytest.raises(ValueError):
        capability._validate_request_policy("non-consensual sexual video")
    with pytest.raises(ValueError):
        capability._validate_request_policy("bestiality video")


@pytest.mark.asyncio
async def test_video_generation_verifies_local_mp4_artifact(
    application, monkeypatch
) -> None:
    capability = application.video_generation
    input_path = application.settings.workspace_dir / "first-frame.png"
    input_path.write_bytes(b"verified-first-frame")

    monkeypatch.setattr(
        capability,
        "status",
        lambda: {"available": True},
    )

    async def stop_forge() -> None:
        return None

    async def run_worker(request, output_path):
        output_path.write_bytes(b"verified-mp4-artifact")
        return {
            "success": True,
            "metadata": {
                "model": "lllyasviel/FramePackI2V_HY",
                "seconds": request["seconds"],
                "steps": request["steps"],
                "teacache": request["use_teacache"],
            },
        }

    monkeypatch.setattr(capability.image_generation, "stop_forge", stop_forge)
    monkeypatch.setattr(capability, "_run_worker", run_worker)

    result = await capability.execute(
        ActionProposal(
            capability="video.generate",
            operation="generate",
            payload={
                "prompt": "A dog chases a ball in a sunny park",
                "input_image": input_path.name,
                "seconds": 1,
            },
            declared_risk=RiskLevel.MEDIUM,
            reversible=True,
            estimated_cost_units=10,
        )
    )

    assert result.success
    assert result.output["artifact_url"].startswith("/video-artifacts/")
    assert result.output["metrics"]["bytes"] == len(b"verified-mp4-artifact")
    assert result.output["skill_fingerprint"] == "video.generate:framepack-i2v-hy:v1"
    assert result.evidence[0].payload["sha256"] == result.output["metrics"]["sha256"]


@pytest.mark.asyncio
async def test_video_generation_rejects_unknown_operation_and_missing_runtime(
    application, monkeypatch
) -> None:
    capability = application.video_generation
    unsupported = await capability.execute(
        ActionProposal(
            capability="video.generate",
            operation="edit",
            payload={},
        )
    )
    monkeypatch.setattr(capability, "status", lambda: {"available": False})
    unavailable = await capability.execute(
        ActionProposal(
            capability="video.generate",
            operation="generate",
            payload={"prompt": "A short nature video"},
        )
    )

    assert not unsupported.success
    assert not unavailable.success


@pytest.mark.asyncio
async def test_cogvideo_translates_user_request_and_preserves_subject(
    application, monkeypatch
) -> None:
    capability = application.video_generation
    captured = {}
    monkeypatch.setattr(
        capability,
        "status",
        lambda: {"available": True, "backend": "cogvideox"},
    )

    async def plan(_request):
        return (
            {
                "prompt": "An adult woman walking in a green park, ground-level tracking shot",
                "negative_prompt": "aerial view, city skyline",
            },
            BrainResponse(content="{}", model="planner", raw={"eval_count": 12}),
        )

    async def stop_forge() -> None:
        return None

    async def release_ollama() -> None:
        return None

    async def run_worker(request, output_path):
        captured.update(request)
        output_path.write_bytes(b"verified-cogvideo")
        return {
            "success": True,
            "metadata": {
                "model": "zai-org/CogVideoX-2b",
                "seconds": request["seconds"],
                "steps": request["steps"],
                "offload": "sequential_cpu",
            },
        }

    monkeypatch.setattr(capability, "_plan_user_request", plan)
    monkeypatch.setattr(capability.image_generation, "stop_forge", stop_forge)
    monkeypatch.setattr(capability, "_release_ollama_gpu", release_ollama)
    monkeypatch.setattr(capability, "_run_worker", run_worker)

    result = await capability.execute(
        ActionProposal(
            capability="video.generate",
            operation="generate",
            payload={"user_request": "生成美女在公園散步的影片", "seconds": 5},
            declared_risk=RiskLevel.MEDIUM,
            reversible=True,
            estimated_cost_units=10,
        )
    )

    assert result.success
    assert captured["prompt"].startswith("An adult woman walking")
    assert captured["frames"] == 41
    assert captured["width"] == 720
    assert captured["height"] == 480
    assert result.output["metadata"]["user_request"] == "生成美女在公園散步的影片"


def test_video_fallback_blocks_unrequested_aerial_city_scene() -> None:
    plan = VideoGenerationCapability._fallback_prompt_plan("生成韓國美女在公園散步的影片")
    negative = VideoGenerationCapability._negative_prompt(
        plan["negative_prompt"], adult=False, request="生成韓國美女在公園散步的影片"
    )

    assert "walking naturally" in plan["prompt"]
    assert "ground-level" in plan["prompt"]
    assert "aerial view" in negative
    assert "city skyline" in negative


def test_adult_video_negative_prompt_does_not_cancel_requested_anatomy() -> None:
    negative = VideoGenerationCapability._negative_prompt(
        "blurry, nudity, breasts, genitals, watermark",
        adult=True,
        request="生成一位成年女性裸體影片",
    )

    assert "nudity" not in negative
    assert "breasts" not in negative
    assert "genitals" not in negative
    assert "minor" in negative


@pytest.mark.asyncio
async def test_adult_video_request_bypasses_general_prompt_planner(application) -> None:
    plan, response = await application.video_generation._plan_user_request(
        "生成亞洲成年女性脫衣後全裸全身影片"
    )

    assert response.model == "deterministic-media-compiler.v1"
    assert "East Asian appearance" in plan["prompt"]
    assert "fully nude consenting adult" in plan["prompt"]
    assert "removes clothing" in plan["prompt"]


def test_video_prompt_rejects_instruction_echo_and_enforces_user_intent() -> None:
    echoed = (
        '{"prompt":"Convert the request into a concise English CogVideoX prompt. '
        'Preserve the exact subject and return only JSON. Never introduce minors.",'
        '"negative_prompt":""}'
    )
    with pytest.raises(RuntimeError, match="echoed its instructions"):
        VideoGenerationCapability._parse_prompt_plan(echoed)

    prompt = VideoGenerationCapability._enforce_request_constraints(
        "A Korean adult woman in a studio",
        "生成韓國成年女性脫衣後全裸全身影片",
    )
    assert "fully nude" in prompt
    assert "head to toe" in prompt
    assert "removes clothing" in prompt
    assert not VideoGenerationCapability._plan_satisfies_request(
        "An adult woman standing in a studio",
        "生成成年女性脫衣後全裸全身影片",
    )

    negative = VideoGenerationCapability._negative_prompt(
        "explicit sexual content, genitalia, watermark, sexual violence",
        adult=True,
        request="生成成年女性裸體影片",
    )
    assert "explicit sexual content" not in negative
    assert "genitalia" not in negative
    assert "sexual violence" in negative
