from __future__ import annotations

import pytest

from eck.app import build_application
from eck.domain.models import LearningThemeCreate


@pytest.mark.asyncio
async def test_autonomous_curriculum_queues_distinct_critical_research(settings) -> None:
    configured = settings.model_copy(
        update={
            "network_enabled": True,
            "autonomous_curriculum_enabled": True,
            "autonomous_curriculum_interval_seconds": 30,
        }
    )
    application = build_application(configured)

    task = await application.autonomous_learning.enqueue_if_idle()
    second = await application.autonomous_learning.enqueue_if_idle()

    assert task is not None
    assert task.action.capability == "web.critical_research"
    assert task.action.payload["source"] == "autonomous"
    assert task.action.payload["topic"].isascii()
    assert task.action.payload["topic"].startswith("agent skill standards")
    assert task.action.payload["url"] == "https://agentskills.io/specification"
    assert "autonomous-curriculum" in task.labels
    assert second is None
    status = application.autonomous_learning.status()
    assert status["active_tasks"] == 1
    assert status["runs_last_24h"] == 1
    assert status["eck_focus_percent"] == 70
    assert status["trusted_community_sources"] >= 11


def test_supervisor_fallback_expands_after_base_topics(application) -> None:
    used = list(application.supervisor._fallback_topics)

    fallback = application.supervisor._next_fallback_topic(used)

    assert fallback
    assert "最新證據更新" in fallback


def test_user_theme_is_persisted_and_expanded_before_general_topics(application) -> None:
    theme = application.store.add_learning_theme(LearningThemeCreate(title="股票"))

    candidates = application.autonomous_learning._candidate_topics("2026-08-09")

    assert theme.active is True
    assert any(item.startswith("股票:") for item in candidates[:12])
    assert any("geopolitical risk" in item for item in candidates if item.startswith("股票:"))
    assert application.autonomous_learning.status()["active_theme_count"] == 1


def test_learning_theme_can_pause_resume_reuse_and_delete(application) -> None:
    store = application.store
    theme = store.add_learning_theme(LearningThemeCreate(title="股票"))
    paused = store.set_learning_theme_active(theme.theme_id, active=False)

    assert paused.active is False
    assert store.list_learning_themes(active_only=True) == []
    reused = store.add_learning_theme(LearningThemeCreate(title="股票"))
    assert reused.theme_id == theme.theme_id
    assert reused.active is True

    with pytest.raises(KeyError, match="Unknown learning theme"):
        store.set_learning_theme_active("missing-theme", active=True)

    store.delete_learning_theme(theme.theme_id)
    with pytest.raises(KeyError, match="Unknown learning theme"):
        store.get_learning_theme(theme.theme_id)
