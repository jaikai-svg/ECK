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
        "blurry, explicit content, nudity, watermark", adult=True
    )
    assert "explicit content" not in negative
    assert "nudity" not in negative
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


def test_verified_image_output_has_stable_skill_identity() -> None:
    metadata = {
        "checkpoint": "CyberRealistic_V90_FP16.safetensors",
        "adetailer": True,
    }

    assert ImageGenerationCapability._skill_fingerprint(metadata) == (
        "image.generate:cyberrealistic-v90-fp16-safetensors:adetailer"
    )
    assert ImageGenerationCapability._skill_name(metadata).endswith(" + ADetailer")
