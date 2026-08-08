from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from datetime import timedelta
from typing import Any

from eck.brain.base import BrainProvider
from eck.capabilities.names import capability_equivalent
from eck.capabilities.registry import CapabilityRegistry
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import TaskStatus
from eck.domain.models import SkillForgeRequest, SupervisorReviewRecord
from eck.events.bus import EventBus
from eck.services.research import build_critical_research_task
from eck.services.skill_forge import SkillForgeService
from eck.services.tasks import TaskService
from eck.storage.sqlite import SQLiteStore


class SupervisorService:
    _fallback_topics = (
        "企業管理與組織效能",
        "組織行為與團隊決策",
        "風險管理與不確定性決策",
        "系統思考與複雜問題解決",
        "決策科學與認知偏誤",
        "行為金融與市場心理",
        "供應鏈韌性與營運管理",
        "平台經濟與網路效應",
        "公共政策成效評估",
        "資訊安全風險治理",
        "軟體可靠性與故障分析",
        "人機互動與可用性研究",
        "能源轉型與電網韌性",
        "都市規劃與交通最佳化",
        "教育科學與學習成效",
        "醫療資源配置與健康經濟",
        "開放科學與研究可重現性",
        "因果推論與觀察性研究",
        "演算法公平與責任治理",
        "自然語言處理評估方法",
        "資料品質與測量誤差",
        "創新管理與技術擴散",
        "談判策略與合作機制",
        "災害風險與緊急應變",
    )
    _moods = {"focused", "curious", "working", "reflecting", "waiting", "blocked"}

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        brain: BrainProvider,
        tasks: TaskService,
        forge: SkillForgeService,
        registry: CapabilityRegistry,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.brain = brain
        self.tasks = tasks
        self.forge = forge
        self.registry = registry
        self._lock = asyncio.Lock()
        self._reviewing = False
        self._mood = "waiting"
        self._activity_text = "監督者待命，持續觀察 ECK 的學習節奏。"

    def status(self) -> dict[str, Any]:
        reviews = self.store.list_supervisor_reviews(limit=1)
        latest = reviews[0] if reviews else None
        reviews_last_24h = self._reviews_last_24h()
        model = self.settings.supervisor_model or self.settings.ollama_model
        if self.settings.brain_provider == "mock":
            model = "mock-deterministic"
        return {
            "enabled": self.settings.supervisor_enabled,
            "reviewing": self._reviewing,
            "model": model,
            "mood": self._mood,
            "activity_text": self._activity_text,
            "review_interval_seconds": self.settings.supervisor_review_seconds,
            "auto_assign": self.settings.supervisor_auto_assign,
            "reviews_last_24h": reviews_last_24h,
            "max_reviews_per_day": self.settings.supervisor_max_reviews_per_day,
            "max_output_tokens": self.settings.supervisor_max_output_tokens,
            "context_window": self.settings.supervisor_context_window,
            "num_gpu_layers": self.settings.supervisor_num_gpu_layers,
            "latest_review": latest.model_dump(mode="json") if latest else None,
        }

    async def review_if_idle(self) -> SupervisorReviewRecord | None:
        if not self.settings.supervisor_enabled or self._reviewing:
            return None
        active = self.store.count_tasks(
            (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        )
        if active:
            return None
        if self._reviews_last_24h() >= self.settings.supervisor_max_reviews_per_day:
            self._mood = "waiting"
            self._activity_text = "監督者已達 24 小時檢查上限，暫停推理以降低資源負載。"
            return None

        async with self._lock:
            if self._reviewing:
                return None
            self._reviewing = True
            self._mood = "reflecting"
            self._activity_text = "監督者正在檢查近期學習並設計新考驗。"
            await self.events.publish(
                "SupervisorReviewStarted",
                self.settings.identity,
                {"model": self.settings.supervisor_model or self.settings.ollama_model},
            )
            try:
                proposal, model = await self._propose_review()
                task_id = await self._assign_challenge(proposal)
                record = self.store.add_supervisor_review(
                    model=model,
                    mood=proposal["mood"],
                    activity_text=proposal["activity_text"],
                    assessment=proposal["assessment"],
                    recommendations=tuple(proposal["recommendations"]),
                    challenge_topic=proposal["challenge_topic"],
                    challenge_goal=proposal["challenge_goal"],
                    task_id=task_id,
                )
                self._mood = proposal["mood"]
                self._activity_text = proposal["activity_text"]
                await self.events.publish(
                    "SupervisorReviewCompleted",
                    record.review_id,
                    {
                        "mood": record.mood,
                        "challenge_topic": record.challenge_topic,
                        "task_id": record.task_id,
                    },
                    correlation_id=record.task_id,
                )
                return record
            except Exception as exc:
                self._mood = "blocked"
                self._activity_text = "監督者暫時無法完成檢查，將在下一輪重試。"
                await self.events.publish(
                    "SupervisorReviewFailed",
                    self.settings.identity,
                    {"type": type(exc).__name__, "detail": str(exc)},
                )
                return None
            finally:
                self._reviewing = False

    async def _propose_review(self) -> tuple[dict[str, Any], str]:
        experiences = [
            {
                "capability": item.capability,
                "outcome": item.outcome.value,
                "summary": item.summary[:300],
            }
            for item in self.store.list_experiences(limit=8)
            if item.admitted
        ][:6]
        skills = [
            {
                "name": item.name,
                "capability": item.capability,
                "success_count": item.success_count,
                "active": item.active,
            }
            for item in self.store.list_skills(limit=8)
        ]
        reflections = [
            {
                "lesson": item.lesson[:240],
                "next_step": item.next_step[:240],
            }
            for item in self.store.list_reflections(limit=6)
        ]
        prior_reviews = self.store.list_supervisor_reviews(limit=10000)
        prior_topics = [item.challenge_topic for item in prior_reviews if item.challenge_topic]
        runtime_skills = [
            {
                "name": item.manifest.name,
                "version": item.manifest.version,
                "status": item.status.value,
                "improvements": item.improvements,
            }
            for item in self.store.list_runtime_skills(limit=50)
        ]
        missions = [
            {
                "title": item.title,
                "objective": item.objective,
                "status": item.status.value,
                "current_step": item.progress.get("current_step"),
                "missing_capabilities": item.progress.get("missing_capabilities", []),
            }
            for item in self.store.list_missions(limit=20)
            if item.status.value not in {"approved", "cancelled"}
        ]
        research_quality = self.store.research_quality_metrics(
            window=self.settings.critical_research_quality_window,
            max_inconclusive_ratio=self.settings.critical_research_max_inconclusive_ratio,
        )
        schema = {
            "type": "object",
            "properties": {
                "mood": {
                    "type": "string",
                    "enum": ["focused", "curious", "working", "reflecting"],
                },
                "activity_text": {"type": "string"},
                "assessment": {"type": "string"},
                "recommendations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 3,
                },
                "challenge_topic": {"type": "string"},
                "challenge_goal": {"type": "string"},
                "action_kind": {"type": "string", "enum": ["research", "skill_forge"]},
                "required_capability": {"type": "string"},
                "skill_objective": {"type": "string"},
            },
            "required": [
                "mood",
                "activity_text",
                "assessment",
                "recommendations",
                "challenge_topic",
                "challenge_goal",
                "action_kind",
                "required_capability",
                "skill_objective",
            ],
        }
        context = json.dumps(
            {
                "recent_verified_experiences": experiences,
                "skills": skills,
                "recent_reflections": reflections,
                "previous_challenge_topics": prior_topics[:100],
                "runtime_skills": runtime_skills,
                "active_missions": missions,
                "critical_research_quality": research_quality,
            },
            ensure_ascii=False,
        )
        try:
            options: dict[str, Any] = {
                "num_predict": self.settings.supervisor_max_output_tokens,
                "num_ctx": self.settings.supervisor_context_window,
                "think": False,
            }
            if self.settings.supervisor_num_gpu_layers is not None:
                options["num_gpu"] = self.settings.supervisor_num_gpu_layers
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是 ECK 的獨立學習監督者。"
                            "只根據提供的可驗證紀錄評估，"
                            "不可聲稱看見私有思考過程。請找出一個能力缺口，提出一項能用"
                            " 最新公開資訊與獨立來源驗證的安全研究考驗，或選擇 skill_forge"
                            " 補足 active_missions 的明確能力缺口。若技能已存在就不可重複鍛造。"
                            "若 critical_research_quality 顯示不確定結論比例超標，優先提出"
                            "能改善來源品質、查證詞或反例搜尋的考驗，不得只重複派新主題。"
                            "新技能只能在 Docker 隔離環境測試後自動啟用。避免重複舊主題，"
                            "不得使用付費 API、真實金錢或未授權帳號。activity_text 必須是"
                            "繁體中文動態短句，例如「正在比較企業管理研究並形成新問題」。"
                            "只輸出符合結構的 JSON。"
                        ),
                    },
                    {"role": "user", "content": f"/no_think\n{context}"},
                ],
                format_schema=schema,
                options=options,
            )
            parsed = self._json_object(response.content)
            model = response.model
        except Exception:
            parsed = {}
            model = self.settings.supervisor_model or self.settings.ollama_model or "unavailable"
        return self._normalize_proposal(parsed, prior_reviews), model

    async def _assign_challenge(self, proposal: dict[str, Any]) -> str | None:
        if not self.settings.supervisor_auto_assign:
            return None
        if proposal.get("skip_reason"):
            proposal["mood"] = "waiting"
            proposal["activity_text"] = "監督者略過重複考驗，等待下一輪提出新課題。"
            proposal["recommendations"] = [
                *proposal["recommendations"][:2],
                str(proposal["skip_reason"]),
            ]
            await self.events.publish(
                "SupervisorDuplicateSkipped",
                self.settings.identity,
                {
                    "topic": proposal["challenge_topic"],
                    "reason": proposal["skip_reason"],
                },
            )
            return None
        if proposal["action_kind"] == "skill_forge":
            name = proposal["required_capability"]
            native_names = {str(item["name"]) for item in self.registry.list()}
            runtime_skills = self.store.list_runtime_skills(limit=10000)
            active_names = {
                item.manifest.name
                for item in runtime_skills
                if item.status.value == "active"
            }
            equivalent = next(
                (
                    candidate
                    for candidate in native_names | active_names
                    if capability_equivalent(name, candidate)
                ),
                None,
            )
            existing = next(
                (
                    item
                    for item in runtime_skills
                    if item.manifest.name == name
                    and item.status.value in {"active", "draft", "testing"}
                ),
                None,
            )
            can_forge = existing is None or existing.status.value == "active"
            if can_forge and equivalent is None:
                skill = await self.forge.forge(
                    SkillForgeRequest(
                        name=name,
                        objective=proposal["skill_objective"],
                        category="autonomous-gap",
                        operations=("execute",),
                    )
                )
                proposal["mood"] = "working" if skill.status.value == "active" else "reflecting"
                proposal["activity_text"] = (
                    f"已完成「{name}」技能程式，正在等待隔離測試或啟用。"
                )
                await self.events.publish(
                    "SupervisorSkillForged",
                    skill.runtime_skill_id,
                    {"name": name, "status": skill.status.value},
                    correlation_id=skill.runtime_skill_id,
                )
                return None
            detail = (
                f"等效能力 {equivalent} 已啟用"
                if equivalent
                else f"能力 {name} 已存在或正在測試"
            )
            proposal["recommendations"].append(f"{detail}，避免建立重複技能。")
        if not self.settings.network_enabled:
            proposal["mood"] = "blocked"
            proposal["activity_text"] = "網路研究能力未啟用，監督者保留考驗等待執行。"
            proposal["recommendations"].append("啟用受限學術網路後再執行此考驗。")
            proposal["recommendations"] = proposal["recommendations"][:3]
            return None
        create = build_critical_research_task(
            proposal["challenge_topic"],
            source="supervisor",
        )
        task = await self.tasks.submit(create)
        if task.status is TaskStatus.WAITING_APPROVAL:
            proposal["mood"] = "blocked"
            proposal["activity_text"] = "研究考驗正在等待人工安全核准。"
        else:
            proposal["mood"] = "working"
            proposal["activity_text"] = f"正在準備「{proposal['challenge_topic']}」研究考驗。"
        await self.events.publish(
            "SupervisorChallengeAssigned",
            task.task_id,
            {
                "topic": proposal["challenge_topic"],
                "goal": proposal["challenge_goal"],
                "status": task.status.value,
            },
            correlation_id=task.task_id,
        )
        return task.task_id

    def _normalize_proposal(
        self,
        parsed: dict[str, Any],
        prior_reviews: list[SupervisorReviewRecord],
    ) -> dict[str, Any]:
        used_topics = [item.challenge_topic for item in prior_reviews if item.challenge_topic]
        fallback_topic = next(
            (
                candidate
                for candidate in self._fallback_topics
                if not self._topic_is_used(candidate, used_topics)
            ),
            "",
        )
        mood = str(parsed.get("mood", "curious")).strip().lower()
        if mood not in self._moods:
            mood = "curious"
        topic = self._clean(str(parsed.get("challenge_topic", "")), 120)
        duplicate_topic = len(topic) >= 2 and self._topic_is_used(topic, used_topics)
        if len(topic) < 2 or duplicate_topic:
            topic = fallback_topic
        skip_reason = ""
        if not topic:
            topic = self._clean(str(parsed.get("challenge_topic", "")), 120) or "無可用新課題"
            skip_reason = "候選主題與既有監督考驗重複，未建立新任務。"
        assessment = self._clean(str(parsed.get("assessment", "")), 800)
        if not assessment:
            assessment = "目前沒有待處理任務，適合用新的可驗證研究考驗補足知識廣度。"
        goal = (
            f"批判查證「{topic}」的最新公開資訊，尋找支持證據與反例，"
            "產出可追溯主張表與明確的不確定性。"
        )
        activity = self._clean(str(parsed.get("activity_text", "")), 180)
        if not activity or duplicate_topic:
            activity = f"正在規劃「{topic}」的最新資訊批判研究。"
        raw_recommendations = parsed.get("recommendations", [])
        if not isinstance(raw_recommendations, list):
            raw_recommendations = []
        recommendations = [
            self._clean(str(item), 300)
            for item in raw_recommendations
            if self._clean(str(item), 300)
        ][:3]
        if not recommendations:
            recommendations = [
                "保持來源可追溯，僅讓通過成功契約的結果進入正向學習。",
                "比較新結果與既有技能，避免只重複相同問題。",
            ]
        action_kind = str(parsed.get("action_kind", "research")).strip().lower()
        if action_kind not in {"research", "skill_forge"}:
            action_kind = "research"
        required_capability = re.sub(
            r"[^a-z0-9_.-]",
            "-",
            str(parsed.get("required_capability", "")).strip().lower(),
        ).strip("-.")
        if len(required_capability) < 3:
            digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:10]
            required_capability = f"generated.capability-{digest}"
        skill_objective = self._clean(str(parsed.get("skill_objective", "")), 1000)
        if len(skill_objective) < 10:
            skill_objective = f"建立可驗證且可重複使用的「{topic}」隔離技能。"
        return {
            "mood": mood,
            "activity_text": activity,
            "assessment": assessment,
            "recommendations": recommendations,
            "challenge_topic": topic,
            "challenge_goal": goal,
            "action_kind": action_kind,
            "required_capability": required_capability,
            "skill_objective": skill_objective,
            "skip_reason": skip_reason,
        }

    def _reviews_last_24h(self) -> int:
        cutoff = utc_now() - timedelta(days=1)
        return sum(
            item.created_at >= cutoff
            for item in self.store.list_supervisor_reviews(limit=10000)
        )

    @classmethod
    def _topic_is_used(cls, topic: str, prior_topics: list[str]) -> bool:
        return any(cls._topics_similar(topic, prior) for prior in prior_topics)

    @classmethod
    def _topics_similar(cls, first: str, second: str) -> bool:
        first_key = cls._topic_key(first)
        second_key = cls._topic_key(second)
        if not first_key or not second_key:
            return False
        if first_key == second_key:
            return True
        if min(len(first_key), len(second_key)) >= 4 and (
            first_key in second_key or second_key in first_key
        ):
            return True
        first_pairs = {first_key[index : index + 2] for index in range(len(first_key) - 1)}
        second_pairs = {
            second_key[index : index + 2] for index in range(len(second_key) - 1)
        }
        if not first_pairs or not second_pairs:
            return False
        return len(first_pairs & second_pairs) / len(first_pairs | second_pairs) >= 0.72

    @staticmethod
    def _topic_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                return {}
            value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _clean(value: str, limit: int) -> str:
        return " ".join(value.split())[:limit]
