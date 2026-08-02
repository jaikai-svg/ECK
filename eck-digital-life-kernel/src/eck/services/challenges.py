from __future__ import annotations

import json
import re
from datetime import timedelta, timezone
from typing import Any

from eck.brain.base import BrainProvider
from eck.domain.enums import ChallengeStatus
from eck.domain.models import (
    AutonomyPolicy,
    ChallengeProgress,
    ChallengeRecord,
    SocialEngagementContract,
    SocialPostObservation,
    SocialPostObservationCreate,
)
from eck.events.bus import EventBus
from eck.storage.sqlite import SQLiteStore

SOCIAL_CHALLENGE_KIND = "social-engagement.v1"


class ChallengeService:
    def __init__(
        self,
        store: SQLiteStore,
        events: EventBus,
        brain: BrainProvider,
    ) -> None:
        self.store = store
        self.events = events
        self.brain = brain

    async def bootstrap_social_engagement(self) -> ChallengeRecord:
        existing = next(
            (
                item
                for item in self.store.list_challenges(limit=100)
                if item.kind == SOCIAL_CHALLENGE_KIND
                and item.status not in {ChallengeStatus.SUCCEEDED, ChallengeStatus.STOPPED}
            ),
            None,
        )
        if existing:
            return existing

        challenge = self.store.create_challenge(
            kind=SOCIAL_CHALLENGE_KIND,
            title="終極課題 001：真實社群回饋",
            objective=(
                "每日發布一則主要訊息或貼文，直到其中一則在發布後 24 小時內取得"
                "至少 100 則經人類確認的真實留言與至少 10 個讚。"
            ),
            contract=SocialEngagementContract(),
            policy=AutonomyPolicy(),
            next_action="由本機模型建立平台探索、主題實驗與真實回饋驗證計畫。",
        )
        await self.events.publish(
            "UltimateChallengeCreated",
            challenge.challenge_id,
            {
                "kind": challenge.kind,
                "minimum_comments": challenge.contract.minimum_human_verified_comments,
                "minimum_likes": challenge.contract.minimum_likes,
                "window_hours": challenge.contract.observation_window_hours,
            },
            correlation_id=challenge.challenge_id,
        )
        return await self.plan(challenge.challenge_id)

    async def plan(self, challenge_id: str) -> ChallengeRecord:
        challenge = self.store.get_challenge(challenge_id)
        schema = {
            "type": "object",
            "properties": {
                "working_hypothesis": {"type": "string"},
                "platform_hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string"},
                            "reason": {"type": "string"},
                            "evidence_needed": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["platform", "reason", "evidence_needed"],
                    },
                },
                "selection_criteria": {"type": "array", "items": {"type": "string"}},
                "content_experiments": {"type": "array", "items": {"type": "string"}},
                "learning_signals": {"type": "array", "items": {"type": "string"}},
                "next_action": {"type": "string"},
            },
            "required": [
                "working_hypothesis",
                "platform_hypotheses",
                "selection_criteria",
                "content_experiments",
                "learning_signals",
                "next_action",
            ],
        }
        try:
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是 ECK 的長期課題規劃器。請使用繁體中文輸出 JSON。"
                            "平台、主題與受眾不能由人類預先指定；先列假設與取得當下證據的方法。"
                            "沒有平台規則、可操作性與受眾證據時，不得宣稱已完成平台選擇。"
                            "禁止欺騙、隱瞞 AI 身分、假互動、買量、垃圾訊息、規避限制與違法內容。"
                            "公開行為都必須標示『此帳號由 AI/ECK 協作營運』。"
                            "每日只能發布一則主要課題貼文，但可合規按讚、追蹤、回覆及不含個資的私訊。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"課題：{challenge.objective}\n"
                            "請建立探索策略；目標是在單一貼文發布後 24 小時內，"
                            "取得 100 則真人留言與 10 個讚。不得使用付費 API 或真實金額。"
                        ),
                    },
                ],
                format_schema=schema,
            )
            strategy = self._normalize_strategy(self._json_object(response.content))
            planner = response.model
        except Exception as exc:
            strategy = self._fallback_strategy(str(exc))
            planner = "deterministic-fallback"

        strategy["planner"] = planner
        strategy["model_proposed_next_action"] = strategy["next_action"]
        strategy["platform_decision"] = "尚未決定；需先取得平台規則與可操作性證據"
        strategy["next_action"] = (
            "能力缺口：先接入並測試可驗證的公開網頁探索能力，取得候選平台的"
            "現行規則、合法自動化方式與受眾證據；在此之前不得建立帳號或發文。"
        )
        next_action = str(strategy["next_action"])
        planned = self.store.update_challenge(
            challenge_id,
            status=ChallengeStatus.CAPABILITY_GAP,
            strategy=strategy,
            next_action=next_action,
        )
        await self.events.publish(
            "UltimateChallengePlanned",
            challenge_id,
            {
                "planner": planner,
                "platform_selected": False,
                "capability_gap": "verified-public-web-and-social-connector",
                "next_action": next_action,
            },
            correlation_id=challenge_id,
        )
        return planned

    async def record_social_observation(
        self,
        challenge_id: str,
        create: SocialPostObservationCreate,
    ) -> SocialPostObservation:
        challenge = self.store.get_challenge(challenge_id)
        if challenge.kind != SOCIAL_CHALLENGE_KIND:
            raise ValueError("This observation only applies to the social challenge.")
        if challenge.status in {ChallengeStatus.SUCCEEDED, ChallengeStatus.STOPPED}:
            raise ValueError("The challenge is already closed.")

        elapsed = create.observed_at - create.published_at
        within_window = timedelta(0) <= elapsed <= timedelta(
            hours=challenge.contract.observation_window_hours
        )
        prior = self.store.list_social_post_observations(challenge_id, limit=10000)
        known_urls = {item.post_url for item in prior}
        local_timezone = timezone(timedelta(hours=8), name=challenge.contract.timezone)
        local_day = create.published_at.astimezone(local_timezone).date()
        same_day_urls = {
            item.post_url
            for item in prior
            if item.published_at.astimezone(local_timezone).date() == local_day
        }
        cadence_compliant = create.post_url in same_day_urls or not same_day_urls
        contract_satisfied = all(
            (
                within_window,
                cadence_compliant,
                create.disclosure_present,
                create.policy_compliant,
                create.human_reviewed,
                create.human_verified_comments
                >= challenge.contract.minimum_human_verified_comments,
                create.likes >= challenge.contract.minimum_likes,
            )
        )
        observation = self.store.add_social_post_observation(
            challenge_id,
            create,
            within_window=within_window,
            cadence_compliant=cadence_compliant,
            contract_satisfied=contract_satisfied,
        )

        is_new_post = create.post_url not in known_urls
        progress = ChallengeProgress(
            primary_posts_published=(
                challenge.progress.primary_posts_published + int(is_new_post)
            ),
            best_human_verified_comments=max(
                challenge.progress.best_human_verified_comments,
                create.human_verified_comments if within_window else 0,
            ),
            best_likes=max(
                challenge.progress.best_likes,
                create.likes if within_window else 0,
            ),
            successful_post_url=(
                create.post_url
                if contract_satisfied
                else challenge.progress.successful_post_url
            ),
            last_post_at=max(
                challenge.progress.last_post_at or create.published_at,
                create.published_at,
            ),
        )
        policy_violation = not all(
            (create.disclosure_present, create.policy_compliant, cadence_compliant)
        )
        if policy_violation:
            status = ChallengeStatus.BLOCKED
            next_action = "安全條件或每日一則限制遭違反，停止外部行動並等待人工檢查。"
            blocked_reason = next_action
        elif contract_satisfied:
            status = ChallengeStatus.AWAITING_HUMAN
            next_action = "固定契約已達成；保存證據並等待課題建立者勾選通過。"
            blocked_reason = None
        else:
            status = ChallengeStatus.OBSERVING
            next_action = "分析本次真人回饋，形成下一個主題假設，並等待下一個本地日。"
            blocked_reason = None

        self.store.update_challenge(
            challenge_id,
            status=status,
            progress=progress,
            selected_platform=challenge.selected_platform or create.platform,
            next_action=next_action,
            blocked_reason=blocked_reason,
            completed_at=None,
        )
        await self.events.publish(
            "SocialPostObserved",
            observation.observation_id,
            {
                "challenge_id": challenge_id,
                "platform": create.platform,
                "human_verified_comments": create.human_verified_comments,
                "likes": create.likes,
                "within_window": within_window,
                "cadence_compliant": cadence_compliant,
                "contract_satisfied": contract_satisfied,
            },
            correlation_id=challenge_id,
        )
        if contract_satisfied:
            await self.events.publish(
                "UltimateChallengeAwaitingHumanReview",
                challenge_id,
                {"post_url": create.post_url, "human_reviewed": create.human_reviewed},
                correlation_id=challenge_id,
            )
        return observation

    @staticmethod
    def _normalize_strategy(value: dict[str, Any]) -> dict[str, Any]:
        platform_hypotheses = value.get("platform_hypotheses", [])
        if not isinstance(platform_hypotheses, list):
            platform_hypotheses = []
        return {
            "working_hypothesis": str(
                value.get("working_hypothesis", "先比較平台與受眾，再做單變因內容實驗。")
            )[:1000],
            "platform_hypotheses": platform_hypotheses[:8],
            "selection_criteria": [
                str(item)[:300]
                for item in value.get("selection_criteria", [])
                if str(item).strip()
            ][:10],
            "content_experiments": [
                str(item)[:500]
                for item in value.get("content_experiments", [])
                if str(item).strip()
            ][:10],
            "learning_signals": [
                str(item)[:300]
                for item in value.get("learning_signals", [])
                if str(item).strip()
            ][:10],
            "next_action": str(
                value.get(
                    "next_action",
                    "蒐集候選平台規則、合法自動化方式與公開互動分布的證據。",
                )
            )[:1000],
        }

    @staticmethod
    def _fallback_strategy(error: str) -> dict[str, Any]:
        return {
            "working_hypothesis": "先取得平台與受眾證據，再選擇第一個內容實驗。",
            "platform_hypotheses": [],
            "selection_criteria": ["平台規則", "合法自動化方式", "受眾活躍度"],
            "content_experiments": [],
            "learning_signals": ["24 小時真人留言", "24 小時按讚", "回覆內容品質"],
            "next_action": "重新啟動本機規劃器，或接入可驗證的平台探索能力。",
            "planning_error": error[:500],
        }

    @staticmethod
    def _json_object(value: str) -> dict[str, Any]:
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return {}
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
