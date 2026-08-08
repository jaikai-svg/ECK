from __future__ import annotations

import pytest

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
