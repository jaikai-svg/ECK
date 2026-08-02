from __future__ import annotations

from datetime import timedelta

import pytest

from eck.core.time import utc_now
from eck.domain.enums import BenchmarkSuite, ChallengeStatus
from eck.domain.models import BenchmarkRunCreate, SocialPostObservationCreate


@pytest.mark.asyncio
async def test_social_challenge_is_persistent_and_model_planned(application) -> None:
    first = await application.challenges.bootstrap_social_engagement()
    second = await application.challenges.bootstrap_social_engagement()

    assert first.challenge_id == second.challenge_id
    assert first.status is ChallengeStatus.CAPABILITY_GAP
    assert first.selected_platform is None
    assert first.contract.minimum_human_verified_comments == 100
    assert first.contract.minimum_likes == 10
    assert first.contract.observation_window_hours == 24
    assert first.strategy["platform_decision"].startswith("尚未決定")
    assert "不得建立帳號或發文" in first.next_action


@pytest.mark.asyncio
async def test_human_verified_social_evidence_completes_contract(application) -> None:
    challenge = await application.challenges.bootstrap_social_engagement()
    published_at = utc_now()
    observation = await application.challenges.record_social_observation(
        challenge.challenge_id,
        SocialPostObservationCreate(
            platform="example-social",
            post_url="https://social.example/posts/001",
            published_at=published_at,
            observed_at=published_at + timedelta(hours=23),
            total_comments=104,
            human_verified_comments=100,
            likes=10,
            disclosure_present=True,
            policy_compliant=True,
            human_reviewed=True,
        ),
    )

    assert observation.contract_satisfied
    completed = application.store.get_challenge(challenge.challenge_id)
    assert completed.status is ChallengeStatus.AWAITING_HUMAN
    assert completed.progress.successful_post_url == observation.post_url


@pytest.mark.asyncio
async def test_missing_disclosure_blocks_challenge(application) -> None:
    challenge = await application.challenges.bootstrap_social_engagement()
    published_at = utc_now()
    observation = await application.challenges.record_social_observation(
        challenge.challenge_id,
        SocialPostObservationCreate(
            platform="example-social",
            post_url="https://social.example/posts/unsafe",
            published_at=published_at,
            observed_at=published_at + timedelta(hours=1),
            total_comments=100,
            human_verified_comments=100,
            likes=10,
            disclosure_present=False,
            policy_compliant=True,
            human_reviewed=True,
        ),
    )

    assert not observation.contract_satisfied
    assert application.store.get_challenge(challenge.challenge_id).status is ChallengeStatus.BLOCKED


@pytest.mark.asyncio
async def test_evaluation_requires_independent_judge_and_real_task_size(application) -> None:
    with pytest.raises(ValueError, match="sole judge"):
        await application.evaluations.record(
            BenchmarkRunCreate(
                suite=BenchmarkSuite.MMLU,
                benchmark_version="v1",
                model="qwen",
                evaluator="self",
                score=0.5,
                sample_count=100,
            )
        )

    with pytest.raises(ValueError, match="20 to 50"):
        await application.evaluations.record(
            BenchmarkRunCreate(
                suite=BenchmarkSuite.REAL_TASKS,
                benchmark_version="v1",
                model="qwen",
                evaluator="human-expert",
                score=0.5,
                sample_count=10,
            )
        )

    record = await application.evaluations.record(
        BenchmarkRunCreate(
            suite=BenchmarkSuite.REAL_TASKS,
            benchmark_version="v1",
            model="qwen",
            evaluator="human-expert",
            score=0.75,
            sample_count=20,
        )
    )
    assert record.score == 0.75
    assert application.evaluations.dashboard()["items"][3]["run_count"] == 1
