from __future__ import annotations

import pytest

from eck.app import build_application
from eck.domain.enums import TaskStatus


@pytest.mark.asyncio
async def test_idle_supervisor_reviews_and_assigns_safe_research(settings) -> None:
    enabled = settings.model_copy(
        update={
            "network_enabled": True,
            "supervisor_enabled": True,
            "supervisor_auto_assign": True,
        }
    )
    application = build_application(enabled)

    review = await application.supervisor.review_if_idle()

    assert review is not None
    assert review.task_id is not None
    assert review.challenge_topic == "企業管理與組織效能"
    assert "最新公開資訊" in review.challenge_goal
    task = application.store.get_task(review.task_id)
    assert task.status is TaskStatus.QUEUED
    assert task.action.capability == "web.critical_research"
    assert task.action.payload["source"] == "supervisor"
    assert application.store.get_task_approval(task.task_id) is None
    assert application.supervisor.status()["latest_review"]["review_id"] == review.review_id


@pytest.mark.asyncio
async def test_supervisor_does_not_interrupt_pending_work(settings) -> None:
    enabled = settings.model_copy(update={"network_enabled": True, "supervisor_enabled": True})
    application = build_application(enabled)
    first = await application.supervisor.review_if_idle()
    assert first is not None

    second = await application.supervisor.review_if_idle()

    assert second is None
    assert len(application.store.list_supervisor_reviews()) == 1


def test_supervisor_replaces_repeated_topic(settings) -> None:
    application = build_application(settings)
    application.store.add_supervisor_review(
        model="test",
        mood="working",
        activity_text="正在研究天體物理學。",
        assessment="既有考驗",
        recommendations=("保留證據",),
        challenge_topic="天體物理學",
        challenge_goal="驗證既有主題",
        task_id=None,
    )

    proposal = application.supervisor._normalize_proposal(
        {
            "challenge_topic": "天體物理學",
            "activity_text": "再次研究天體物理學",
            "action_kind": "research",
        },
        application.store.list_supervisor_reviews(limit=10000),
    )

    assert proposal["challenge_topic"] != "天體物理學"
    assert "天體物理學" not in proposal["activity_text"]


@pytest.mark.asyncio
async def test_supervisor_does_not_forge_equivalent_browser_skill(settings) -> None:
    enabled = settings.model_copy(update={"network_enabled": True})
    application = build_application(enabled)
    before = len(application.store.list_runtime_skills(limit=10000))
    proposal = application.supervisor._normalize_proposal(
        {
            "challenge_topic": "公開網頁證據品質",
            "action_kind": "skill_forge",
            "required_capability": "browser.public_explore",
            "skill_objective": "建立公開網頁探索能力並驗證來源。",
        },
        [],
    )

    task_id = await application.supervisor._assign_challenge(proposal)

    assert task_id is not None
    assert len(application.store.list_runtime_skills(limit=10000)) == before
    assert any("等效能力" in item for item in proposal["recommendations"])


@pytest.mark.asyncio
async def test_supervisor_daily_limit_skips_model_inference(settings) -> None:
    limited = settings.model_copy(
        update={"supervisor_enabled": True, "supervisor_max_reviews_per_day": 1}
    )
    application = build_application(limited)
    application.store.add_supervisor_review(
        model="test",
        mood="waiting",
        activity_text="已完成今日檢查。",
        assessment="每日上限測試",
        recommendations=("等待下一時段",),
        challenge_topic="已完成主題",
        challenge_goal="不再重複推理",
        task_id=None,
    )

    review = await application.supervisor.review_if_idle()

    assert review is None
    assert "24 小時檢查上限" in application.supervisor.status()["activity_text"]
