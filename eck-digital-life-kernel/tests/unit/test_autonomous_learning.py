from __future__ import annotations

import pytest

from eck.app import build_application


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
    assert status["trusted_community_sources"] == 8


def test_supervisor_fallback_expands_after_base_topics(application) -> None:
    used = list(application.supervisor._fallback_topics)

    fallback = application.supervisor._next_fallback_topic(used)

    assert fallback
    assert "最新證據更新" in fallback
