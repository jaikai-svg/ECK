from __future__ import annotations

from typing import Any

import pytest

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource
from eck.domain.models import CapabilityResult, Evidence
from eck.services.dialogue import DialogueService


class RecordingBrain(BrainProvider):
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def health(self) -> BrainHealth:
        return BrainHealth(provider="recording", available=True, model="recording")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        self.messages = messages
        return BrainResponse(
            content="我是通用 ECK 對話介面。",
            model="recording",
            raw={"eval_count": 8},
        )


@pytest.mark.parametrize(
    "message",
    [
        "請生成一張狗狗圖片",
        "幫我畫一張太空城市插畫",
        "Create a high-quality dog image",
    ],
)
def test_detects_image_generation_requests(message: str) -> None:
    assert DialogueService.is_image_request(message)


@pytest.mark.parametrize(
    "message",
    ["請介紹你自己", "研究狗的行為", "這張圖片的來源是什麼？"],
)
def test_does_not_route_non_generation_requests_to_image_engine(message: str) -> None:
    assert not DialogueService.is_image_request(message)


@pytest.mark.parametrize(
    "message",
    ["請移除上一張圖片背景", "把照片背景變透明", "remove the image background"],
)
def test_detects_background_removal_requests(message: str) -> None:
    assert DialogueService.is_background_removal_request(message)


@pytest.mark.parametrize(
    "message",
    ["生成一段狗狗玩球的影片", "製作一個短動畫", "Create a short video clip"],
)
def test_detects_video_generation_requests(message: str) -> None:
    assert DialogueService.is_video_request(message)


def test_hidden_thought_markup_is_not_returned() -> None:
    content = "<think>internal draft</think>\n\nVisible answer"
    assert DialogueService._visible_content(content) == "Visible answer"
    assert DialogueService._answer_content('{"answer":"Structured answer"}') == (
        "Structured answer"
    )


async def test_general_dialogue_uses_generic_role_and_no_research_payload(application) -> None:
    brain = RecordingBrain()
    application.brain = brain

    result = await DialogueService(application).respond(
        "請介紹你自己",
        [{"role": "assistant", "content": "我是專注於文本分析的學術研究助理。"}],
    )

    system_prompt = brain.messages[0]["content"]
    assert system_prompt.startswith("/no_think")
    assert brain.messages[-1]["content"].startswith("/no_think")
    assert "通用對話與任務介面" in system_prompt
    assert '"research_results": []' in system_prompt
    assert result["tool"] is None
    assert result["inference"] == {"eval_count": 8}


@pytest.mark.asyncio
async def test_dialogue_executes_and_returns_verified_image(application, monkeypatch) -> None:
    capability = application.image_generation
    monkeypatch.setattr(capability, "status", lambda: {"available": True})

    async def generate(action) -> CapabilityResult:
        started = utc_now()
        return CapabilityResult(
            action_id=action.action_id,
            capability="image.generate",
            success=True,
            output={
                "artifact": "dog.png",
                "artifact_url": "/artifacts/dog.png",
                "artifact_path": "generated_images/dog.png",
                "metadata": {
                    "width": 512,
                    "height": 512,
                    "model": "Realistic Vision V6",
                    "backend": "forge",
                    "seed": 42,
                    "total_elapsed_seconds": 12.5,
                    "prompt_planner_model": "qwen3:4b",
                    "prompt_planner_inference": {"eval_count": 32},
                },
                "metrics": {"completed": True},
                "skill_fingerprint": "image.generate:test",
                "skill_name": "Verified image test",
            },
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="A local PNG was generated and verified.",
                    payload={"artifact": "dog.png"},
                ),
            ),
            reversible=True,
            cost_units=12.5,
            started_at=started,
            finished_at=utc_now(),
        )

    monkeypatch.setattr(capability, "execute", generate)

    result = await DialogueService(application).respond(
        "請生成一張狗狗玩球的圖片", []
    )

    assert result["tool"] == "image.generate"
    assert result["artifacts"][0]["url"] == "/artifacts/dog.png"
    assert result["inference"] == {"eval_count": 32}
    assert "已通過檔案、尺寸與雜湊驗證" in result["answer"]
