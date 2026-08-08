from __future__ import annotations

import pytest

from eck.capabilities.image_generation import ImageGenerationCapability


def test_image_prompt_plan_requires_usable_english() -> None:
    plan = ImageGenerationCapability._parse_plan(
        '{"prompt":"a golden retriever puppy in a meadow",'
        '"negative_prompt":"watermark"}'
    )
    assert plan["prompt"] == "a golden retriever puppy in a meadow"

    with pytest.raises(RuntimeError, match="usable English prompt"):
        ImageGenerationCapability._parse_plan(
            '{"prompt":"一隻黃金獵犬", "negative_prompt":""}'
        )


def test_image_prompt_quality_blocks_common_text_artifacts() -> None:
    prompt = ImageGenerationCapability._quality_prompt("a dog")
    negative = ImageGenerationCapability._negative_prompt("oversaturated")
    assert "high quality" in prompt
    assert "watermark" in negative
    assert "typography" in negative


def test_explicit_model_name_takes_priority_and_adult_negative_is_preserved() -> None:
    assert (
        ImageGenerationCapability._requested_model_alias(
            "Use CyberRealistic for this photo"
        )
        == "cyberrealistic"
    )
    assert (
        ImageGenerationCapability._requested_model_alias("使用 ChilloutMix 生成")
        == "chilloutmix"
    )
    negative = ImageGenerationCapability._negative_prompt(
        "blurry, explicit content, nudity, breasts, genitals, watermark", adult=True
    )
    assert "explicit content" not in negative
    assert "nudity" not in negative
    assert "breasts" not in negative
    assert "genitals" not in negative
    assert "minor" in negative


def test_image_prompt_removes_planner_model_notes_and_detects_people() -> None:
    prompt = ImageGenerationCapability._strip_model_selection_artifacts(
        "A dog playing with a ball, Realistic Vision, ADetailer, sunny park"
    )

    assert prompt == "A dog playing with a ball, sunny park"
    assert not ImageGenerationCapability._prompt_depicts_people("一隻狗狗在公園玩球")
    assert ImageGenerationCapability._prompt_depicts_people("一位成年女性的全身照片")


def test_legal_adult_content_is_enabled_but_abusive_content_is_blocked(
    application,
) -> None:
    capability = application.image_generation

    assert capability._validate_request_policy("一位成年人的藝術人體藝術攝影，裸體")

    with pytest.raises(ValueError, match="minors"):
        capability._validate_request_policy("未成年兒童裸體情色照片")
    with pytest.raises(ValueError, match="Non-consensual"):
        capability._validate_request_policy("non-consensual sexual scene")
    with pytest.raises(ValueError, match="animals"):
        capability._validate_request_policy("bestiality image")


def test_adult_prompt_planner_has_deterministic_fallback() -> None:
    plan = ImageGenerationCapability._fallback_prompt_plan(
        "生成一張 9:16 韓國成年女性全裸全身照片"
    )

    assert "fully nude adult" in plan["prompt"]
    assert "South Korean" in plan["prompt"]
    assert "head to toe" in plan["prompt"]
    assert plan["model"] == "chilloutmix"


@pytest.mark.asyncio
async def test_adult_image_request_bypasses_general_prompt_planner(application) -> None:
    plan, response = await application.image_generation._plan_user_request(
        "生成亞洲成年女性全裸全身圖片"
    )

    assert response.model == "deterministic-media-compiler.v1"
    assert "East Asian appearance" in plan["prompt"]
    assert "fully nude adult" in plan["prompt"]
    assert "head to toe" in plan["prompt"]


def test_image_prompt_rejects_instruction_echo_and_enforces_user_intent() -> None:
    echoed = (
        '{"prompt":"Convert the user request into a prompt. Select realistic_vision. '
        'Never introduce minors and return only the requested JSON.",'
        '"negative_prompt":"","model":"realistic_vision",'
        '"use_adetailer":false}'
    )
    with pytest.raises(RuntimeError, match="echoed its instructions"):
        ImageGenerationCapability._parse_plan(echoed)

    prompt = ImageGenerationCapability._enforce_request_constraints(
        "A South Korean adult woman in a studio",
        "生成 9:16 韓國成年女性全裸全身入鏡圖片",
    )
    assert "fully nude" in prompt
    assert "head to toe" in prompt
    assert "vertical 9:16" in prompt
    assert not ImageGenerationCapability._plan_satisfies_request(
        "A Korean woman wearing a white dress, portrait",
        "生成韓國成年女性全裸全身圖片",
    )
    assert ImageGenerationCapability._recommended_model_alias(
        "韓國成年女性人體藝術"
    ) == "chilloutmix"

    negative = ImageGenerationCapability._negative_prompt(
        "explicit sexual content, genitalia, clothing, sexual violence",
        adult=True,
        request="成年女性全身裸體",
    )
    assert "explicit sexual content" not in negative
    assert "genitalia" not in negative
    assert "clothing" in negative
    assert "sexual violence" in negative
    assert "cropped" in negative


def test_verified_image_output_has_stable_skill_identity() -> None:
    metadata = {
        "checkpoint": "CyberRealistic_V90_FP16.safetensors",
        "adetailer": True,
    }

    assert ImageGenerationCapability._skill_fingerprint(metadata) == (
        "image.generate:cyberrealistic-v90-fp16-safetensors:adetailer"
    )
    assert ImageGenerationCapability._skill_name(metadata).endswith(" + ADetailer")
