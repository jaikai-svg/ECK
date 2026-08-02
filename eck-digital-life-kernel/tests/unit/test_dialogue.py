from __future__ import annotations

from typing import Any

import pytest

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse
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
