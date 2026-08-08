from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import TaskStatus
from eck.domain.models import TaskRecord
from eck.events.bus import EventBus
from eck.services.research import build_critical_research_task
from eck.services.tasks import TaskService
from eck.storage.sqlite import SQLiteStore


class AutonomousLearningService:
    _domains = (
        "business management and organizational effectiveness",
        "organizational behavior and team decision making",
        "risk management and decisions under uncertainty",
        "systems thinking and complex problem solving",
        "decision science and cognitive bias",
        "behavioral finance and market psychology",
        "supply chain resilience and operations management",
        "platform economics and network effects",
        "public policy impact evaluation",
        "cybersecurity risk governance",
        "software reliability and failure analysis",
        "human computer interaction and usability",
        "energy transition and grid resilience",
        "urban planning and transport optimization",
        "education science and learning outcomes",
        "health resource allocation and health economics",
        "open science and research reproducibility",
        "causal inference and observational studies",
        "algorithmic fairness and accountable governance",
        "natural language processing evaluation",
        "data quality and measurement error",
        "innovation management and technology diffusion",
        "negotiation strategy and cooperation mechanisms",
        "disaster risk and emergency response",
    )
    _lenses = (
        "current evidence and credibility",
        "counterexamples and failure cases",
        "competing explanations",
        "measurement methods and reproducibility",
        "cross-domain applications and limits",
        "recent changes and open questions",
    )
    _terminal_statuses = {
        TaskStatus.VERIFIED_SUCCESS,
        TaskStatus.VERIFIED_FAILURE,
        TaskStatus.UNVERIFIABLE,
        TaskStatus.CONSTRAINT_VIOLATION,
        TaskStatus.BLOCKED,
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        tasks: TaskService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.tasks = tasks
        self._activity_text = "自主課程器待命。"
        self._last_task_id: str | None = None

    def status(self) -> dict[str, Any]:
        now = utc_now()
        tasks = self._autonomous_tasks()
        recent = [item for item in tasks if item.created_at >= now - timedelta(days=1)]
        active = [
            item
            for item in recent
            if item.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}
        ]
        verified = [item for item in recent if item.status is TaskStatus.VERIFIED_SUCCESS]
        return {
            "enabled": self.settings.autonomous_curriculum_enabled,
            "activity_text": self._activity_text,
            "interval_seconds": self.settings.autonomous_curriculum_interval_seconds,
            "max_runs_per_day": self.settings.autonomous_curriculum_max_runs_per_day,
            "runs_last_24h": len(recent),
            "verified_last_24h": len(verified),
            "active_tasks": len(active),
            "last_task_id": self._last_task_id,
        }

    async def enqueue_if_idle(self) -> TaskRecord | None:
        if not self.settings.autonomous_curriculum_enabled:
            self._activity_text = "自主課程器已停用。"
            return None
        if not self.settings.network_enabled or not self.settings.critical_research_enabled:
            self._activity_text = "自主課程等待唯讀網路研究能力。"
            return None
        active = self.store.count_tasks(
            (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        )
        if active:
            return None

        now = utc_now()
        tasks = self._autonomous_tasks()
        recent = [item for item in tasks if item.created_at >= now - timedelta(days=1)]
        if len(recent) >= self.settings.autonomous_curriculum_max_runs_per_day:
            self._activity_text = "自主課程已達 24 小時資源上限，等待滾動視窗釋放。"
            return None
        if tasks:
            elapsed = (now - tasks[0].created_at).total_seconds()
            if elapsed < self.settings.autonomous_curriculum_interval_seconds:
                self._activity_text = "正在整理上一輪證據，等待下一個學習時槽。"
                return None

        topic = self._next_topic(now, tasks)
        if topic is None:
            self._activity_text = "今日自主研究矩陣已完成，等待明日新資訊。"
            return None
        task = await self.tasks.submit(
            build_critical_research_task(topic, source="autonomous")
        )
        self._last_task_id = task.task_id
        self._activity_text = f"正在自主查證「{topic}」。"
        await self.events.publish(
            "AutonomousLearningQueued",
            task.task_id,
            {"topic": topic, "status": task.status.value},
            correlation_id=task.task_id,
        )
        return task

    def _next_topic(
        self,
        now: datetime,
        tasks: list[TaskRecord],
    ) -> str | None:
        day = now.date().isoformat()
        used = {
            str(item.action.payload.get("topic", ""))
            for item in tasks
            if item.created_at.date() == now.date()
        }
        candidates = (
            f"{domain}: {lens} ({day})"
            for lens in self._lenses
            for domain in self._domains
        )
        return next((candidate for candidate in candidates if candidate not in used), None)

    def _autonomous_tasks(self) -> list[TaskRecord]:
        return [
            item
            for item in self.store.list_tasks(limit=10000)
            if "autonomous-curriculum" in item.labels
            and item.status in self._terminal_statuses
            | {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}
        ]
