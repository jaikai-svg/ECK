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
    ["生成一個裸體的成年女性", "生成美女在公園散步", "create a nude adult woman"],
)
def test_detects_implicit_visual_generation_requests(message: str) -> None:
    assert DialogueService.is_image_request(message)


@pytest.mark.parametrize(
    "message",
    ["請介紹你自己", "研究狗的行為", "這張圖片的來源是什麼？"],
)
def test_does_not_route_non_generation_requests_to_image_engine(message: str) -> None:
    assert not DialogueService.is_image_request(message)


def test_does_not_route_requested_text_output_to_image_engine() -> None:
    assert not DialogueService.is_image_request("生成一篇美女角色介紹文章")


def test_dialogue_extracts_image_aspect_ratio_and_video_duration(application) -> None:
    dialogue = DialogueService(application)

    assert dialogue._image_dimensions("生成 9:16 全身圖片") == (504, 896)
    assert dialogue._image_dimensions("生成 16:9 圖片") == (896, 504)
    assert dialogue._image_dimensions("生成 2:3 圖片") == (512, 768)
    assert dialogue._image_dimensions("生成 3:2 圖片") == (768, 512)
    assert dialogue._image_dimensions("生成直式全身照") == (512, 768)
    assert dialogue._image_dimensions("生成橫式風景圖") == (768, 512)
    assert dialogue._video_seconds("生成 6 秒影片") == 6.0
    assert dialogue._video_seconds("生成 30 秒影片") == 6.0
    assert dialogue._video_dimensions("生成 9:16 全身影片") == (432, 768)
    assert dialogue._video_dimensions("生成 16:9 橫式影片") == (768, 432)


def test_command_catalog_exposes_builtin_shortcuts() -> None:
    commands = {item["command"]: item for item in DialogueService.command_catalog()}

    assert commands["/image 9:16"]["insert"] == "/image 9:16 "
    assert commands["/video 9:16"]["requires_prompt"] is True
    assert commands["/status"]["requires_prompt"] is False
    assert commands["/help"]["category"] == "系統"


@pytest.mark.asyncio
async def test_builtin_status_and_help_commands_do_not_call_brain(application) -> None:
    dialogue = DialogueService(application)

    status = await dialogue.respond("/status", [])
    help_response = await dialogue.respond("/help", [])

    assert status["tool"] == "system.status"
    assert status["model"] == "eck-command-router.v1"
    assert status["context"]["verified_experiences"] == 0
    assert help_response["tool"] == "system.help"
    assert "/image" in help_response["answer"]


def test_slash_media_commands_route_and_preserve_options() -> None:
    image = DialogueService._parse_media_command(
        "/image nsfw 9:16 一位成年女性全身入鏡"
    )
    video = DialogueService._parse_media_command("/video 16:9 5s 狗狗在公園玩球")

    assert image == {
        "kind": "image",
        "nsfw": True,
        "ratio": "9:16",
        "seconds": None,
        "prompt": "一位成年女性全身入鏡",
    }
    assert video == {
        "kind": "video",
        "nsfw": False,
        "ratio": "16:9",
        "seconds": 5.0,
        "prompt": "狗狗在公園玩球",
    }
    assert DialogueService.is_image_request("/image 一隻狗")
    assert DialogueService.is_video_request("/video 一隻狗玩球")
    normalized = DialogueService._normalize_media_command(image)
    assert "legal adult NSFW" in normalized
    assert "aspect ratio 9:16" in normalized

    with pytest.raises(ValueError, match="需要提供生成內容"):
        DialogueService._parse_media_command("/image nsfw 9:16")


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


@pytest.mark.asyncio
async def test_dialogue_reports_unavailable_image_engine(application, monkeypatch) -> None:
    monkeypatch.setattr(
        application.image_generation,
        "status",
        lambda: {"available": False},
    )

    with pytest.raises(RuntimeError, match="本機圖像引擎尚未就緒"):
        await DialogueService(application).respond("生成一張狗狗圖片", [])


@pytest.mark.asyncio
async def test_dialogue_returns_video_resource_block_without_http_failure(
    application, monkeypatch
) -> None:
    monkeypatch.setattr(
        application.video_generation,
        "status",
        lambda: {
            "available": False,
            "checks": {"python": True, "model": True},
            "resources": {"detail": "System RAM is below the verified minimum."},
        },
    )

    result = await DialogueService(application).respond("生成一段狗狗玩球的影片", [])

    assert result["tool"] == "video.generate"
    assert result["blocked"] is True
    assert result["pending"] is False
    assert result["artifacts"] == []
    assert "不會建立虛假成果" in result["answer"]


@pytest.mark.asyncio
async def test_dialogue_executes_and_returns_verified_video(application, monkeypatch) -> None:
    capability = application.video_generation
    captured_payload: dict[str, Any] = {}
    monkeypatch.setattr(
        capability,
        "status",
        lambda: {
            "available": True,
            "backend": "cogvideox",
            "model": "zai-org/CogVideoX-2b",
        },
    )

    async def generate(action) -> CapabilityResult:
        captured_payload.update(action.payload)
        started = utc_now()
        return CapabilityResult(
            action_id=action.action_id,
            capability="video.generate",
            success=True,
            output={
                "artifact": "walking.mp4",
                "artifact_url": "/video-artifacts/walking.mp4",
                "artifact_path": "generated_videos/walking.mp4",
                "metadata": {
                    "model": "zai-org/CogVideoX-2b",
                    "backend": "cogvideox",
                    "seconds": 5.0,
                    "width": 720,
                    "height": 480,
                },
                "metrics": {"seconds": 5.0, "completed": True},
                "skill_fingerprint": "video.generate:cogvideox-2b:test",
                "skill_name": "Verified CogVideoX test",
            },
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="A local MP4 was generated and verified.",
                    payload={"artifact": "walking.mp4"},
                ),
            ),
            reversible=True,
            cost_units=60,
            started_at=started,
            finished_at=utc_now(),
        )

    monkeypatch.setattr(capability, "execute", generate)

    result = await DialogueService(application).respond(
        "生成 5 秒美女在公園散步的影片", []
    )

    assert captured_payload == {
        "user_request": "生成 5 秒美女在公園散步的影片",
        "seconds": 5.0,
        "width": 720,
        "height": 480,
    }
    assert result["tool"] == "video.generate"
    assert result["artifacts"][0]["url"] == "/video-artifacts/walking.mp4"
    assert result["artifacts"][0]["metadata"]["seconds"] == 5.0
    assert "已生成並通過 MP4 檔案驗證" in result["answer"]


@pytest.mark.asyncio
async def test_dialogue_executes_and_returns_background_removed_image(
    application, monkeypatch
) -> None:
    capability = application.image_background_removal
    monkeypatch.setattr(capability, "status", lambda: {"available": True})

    async def remove(action) -> CapabilityResult:
        started = utc_now()
        return CapabilityResult(
            action_id=action.action_id,
            capability="image.remove_background",
            success=True,
            output={
                "artifact": "dog-transparent.png",
                "artifact_url": "/artifacts/dog-transparent.png",
                "artifact_path": "generated_images/dog-transparent.png",
                "metadata": {
                    "model": "birefnet-general",
                    "transparent_background": True,
                },
                "metrics": {"completed": True},
                "skill_fingerprint": "image.remove_background:birefnet:test",
                "skill_name": "Verified background removal test",
            },
            evidence=(
                Evidence(
                    source=EvidenceSource.TOOL,
                    claim="A transparent local PNG was generated and verified.",
                    payload={"artifact": "dog-transparent.png"},
                ),
            ),
            reversible=True,
            cost_units=10,
            started_at=started,
            finished_at=utc_now(),
        )

    monkeypatch.setattr(capability, "execute", remove)

    result = await DialogueService(application).respond("移除上一張圖片背景", [])

    assert result["tool"] == "image.remove_background"
    assert result["artifacts"][0]["url"] == "/artifacts/dog-transparent.png"
    assert result["artifacts"][0]["metadata"]["transparent_background"] is True
    assert "移除最近生成圖片的背景" in result["answer"]
