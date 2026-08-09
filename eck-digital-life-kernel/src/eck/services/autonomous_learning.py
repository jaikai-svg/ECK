from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import TaskStatus
from eck.domain.models import TaskRecord
from eck.events.bus import EventBus
from eck.services.community_sources import CommunitySourceCatalog
from eck.services.research import build_critical_research_task
from eck.services.self_model import RepositorySelfModelService
from eck.services.tasks import TaskService
from eck.storage.sqlite import SQLiteStore


class AutonomousLearningService:
    _self_development_domains = (
        "ECK repository architecture and dependency impact",
        "ECK failing generated skills and executable repair",
        "ECK resource bottlenecks and background scheduling",
        "ECK test coverage regression and failure localization",
        "ECK capability gaps and reusable worker design",
        "ECK memory retrieval and verified experience reuse",
        "ECK prompt tool and model routing reliability",
        "ECK portability rollback and version lineage",
    )
    _eck_domains = (
        "agent skill standards and reusable tool interfaces",
        "LLM agent planning and hierarchical task decomposition",
        "durable agent execution and checkpoint recovery",
        "workflow automation and event driven orchestration",
        "tool use evaluation and function calling reliability",
        "agent memory RAG and retrieval evaluation",
        "model context protocol security and capability isolation",
        "sandboxed code execution for autonomous agents",
        "self improving agents with verified feedback",
        "automated skill synthesis and regression testing",
        "local LLM inference quantization and GPU optimization",
        "agent benchmarks and real world task evaluation",
        "multi agent coordination and supervisor design",
        "browser agents and policy compliant web automation",
        "open source agent frameworks comparative evidence",
        "AI workflow observability and trace evaluation",
        "prompt injection and untrusted content isolation",
        "agent tool selection and capability routing",
        "autonomous research and evidence synthesis",
        "model routing and local inference scheduling",
        "context engineering and long horizon memory",
        "data provenance and tamper evident learning",
        "synthetic training data and verifier design",
        "self modification safety and rollback architecture",
    )
    _general_domains = (
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
    _foundation_domains = (
        "browser automation and public web evidence collection",
        "document spreadsheet and presentation generation",
        "data analysis visualization and reproducible reporting",
        "software project planning testing and packaging",
        "local multimedia generation and artifact verification",
        "human computer interaction and usable interface design",
    )
    _lenses = (
        "current evidence and credibility",
        "counterexamples and failure cases",
        "competing explanations",
        "measurement methods and reproducibility",
        "cross-domain applications and limits",
        "recent changes and open questions",
    )
    _theme_lenses = (
        "fundamentals mechanisms and terminology",
        "data measurement and analytical methods",
        "economic policy and institutional effects",
        "social behavioral and ethical effects",
        "geopolitical risk and external shocks",
        "practical tools workflows and reusable skills",
        "counterexamples limitations and failure cases",
        "recent evidence changes and open questions",
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
        community_sources: CommunitySourceCatalog,
        self_model: RepositorySelfModelService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.tasks = tasks
        self.community_sources = community_sources
        self.self_model = self_model
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
        themes = self.store.list_learning_themes(limit=100)
        return {
            "enabled": self.settings.autonomous_curriculum_enabled,
            "activity_text": self._activity_text,
            "interval_seconds": self.settings.autonomous_curriculum_interval_seconds,
            "max_runs_per_day": self.settings.autonomous_curriculum_max_runs_per_day,
            "eck_focus_percent": (
                self.settings.p5_self_development_percent
                + self.settings.p5_ai_research_percent
            ),
            "trusted_community_sources": self.community_sources.status()["source_count"],
            "runs_last_24h": len(recent),
            "verified_last_24h": len(verified),
            "active_tasks": len(active),
            "last_task_id": self._last_task_id,
            "learning_themes": [item.model_dump(mode="json") for item in themes],
            "active_theme_count": sum(item.active for item in themes),
            "theme_focus_percent": self.settings.p5_exploration_percent,
            "portfolio": self.portfolio(),
            "detected_gaps": self._detected_gaps(),
        }

    def portfolio(self) -> dict[str, int]:
        return {
            "self_development": self.settings.p5_self_development_percent,
            "ai_research": self.settings.p5_ai_research_percent,
            "foundation": self.settings.p5_foundation_percent,
            "exploration": self.settings.p5_exploration_percent,
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
        if (
            self.settings.autonomous_curriculum_max_runs_per_day > 0
            and len(recent) >= self.settings.autonomous_curriculum_max_runs_per_day
        ):
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
        trusted_source = self.community_sources.match(topic)
        task = await self.tasks.submit(
            build_critical_research_task(
                topic,
                url=str(trusted_source["url"]) if trusted_source else None,
                source="autonomous",
            )
        )
        self._last_task_id = task.task_id
        self._activity_text = f"正在自主查證「{topic}」。"
        await self.events.publish(
            "AutonomousLearningQueued",
            task.task_id,
            {
                "topic": topic,
                "status": task.status.value,
                "trusted_source_id": (
                    trusted_source["source_id"] if trusted_source else None
                ),
            },
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
        candidates = self._candidate_topics(day)
        return next((candidate for candidate in candidates if candidate not in used), None)

    def _candidate_topics(self, day: str) -> list[str]:
        self_development = [
            f"{domain}: {lens} ({day})"
            for lens in self._lenses
            for domain in self._self_development_domains
        ]
        ai_research = [
            f"{domain}: {lens} ({day})"
            for lens in self._lenses
            for domain in self._eck_domains
        ]
        foundation = [
            f"{domain}: {lens} ({day})"
            for lens in self._lenses
            for domain in self._foundation_domains
        ]
        exploration = [
            f"{domain}: {lens} ({day})"
            for lens in self._lenses
            for domain in self._general_domains
        ]
        themes = [
            f"{theme.title}: {lens} ({day})"
            for lens in self._theme_lenses
            for theme in self.store.list_learning_themes(active_only=True, limit=100)
        ]
        exploration = themes + exploration
        groups = (
            (self_development, self.settings.p5_self_development_percent),
            (ai_research, self.settings.p5_ai_research_percent),
            (foundation, self.settings.p5_foundation_percent),
            (exploration, self.settings.p5_exploration_percent),
        )
        merged: list[str] = []
        while any(items for items, _ in groups):
            for items, percent in groups:
                slots = percent // 5
                if not slots:
                    continue
                merged.extend(items[:slots])
                del items[:slots]
        return merged

    def _detected_gaps(self) -> list[dict[str, Any]]:
        model = self.self_model.status()
        gaps: list[dict[str, Any]] = []
        if not model.get("initialized") or model.get("stale"):
            gaps.append(
                {
                    "kind": "self_model",
                    "priority": 100,
                    "detail": "Repository self-model is missing or stale.",
                }
            )
        failed_generated = [
            item
            for item in self.store.list_runtime_skills(limit=1000)
            if item.source == "eck-generated" and item.status.value == "failed"
        ]
        if failed_generated:
            gaps.append(
                {
                    "kind": "skill_repair",
                    "priority": 90,
                    "detail": f"{len(failed_generated)} generated skill versions remain failed.",
                }
            )
        architecture = model.get("architecture", {})
        test_targets = {
            str(item.get("target", ""))
            for item in architecture.get("test_edges", [])
            if isinstance(item, dict)
        }
        core_modules = [
            item
            for item in model.get("python_modules", [])
            if isinstance(item, dict) and item.get("kind") == "core"
        ]
        modules_without_direct_tests = sum(
            not any(
                target == self.self_model._module_name(str(item.get("path", "")))
                or target.startswith(
                    f"{self.self_model._module_name(str(item.get('path', '')))}."
                )
                for target in test_targets
            )
            for item in core_modules
        )
        if modules_without_direct_tests:
            gaps.append(
                {
                    "kind": "test_mapping",
                    "priority": 70,
                    "detail": (
                        f"{modules_without_direct_tests} core modules have no direct import edge "
                        "from tests; dynamic coverage still requires measurement."
                    ),
                }
            )
        return sorted(gaps, key=lambda item: int(item["priority"]), reverse=True)

    def _autonomous_tasks(self) -> list[TaskRecord]:
        since = utc_now() - timedelta(days=1)
        return self.store.list_tasks_with_label(
            "autonomous-curriculum",
            since=since,
            limit=1000,
        )
