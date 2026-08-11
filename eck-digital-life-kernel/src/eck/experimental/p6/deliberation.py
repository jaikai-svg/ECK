from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from eck.brain.base import BrainProvider
from eck.domain.models import MissionRecord, MissionStepRecord


@dataclass(frozen=True, slots=True)
class DeliberationResult:
    role: str
    role_label: str
    summary: str
    rounds: int
    ready_for_action: bool
    model: str


class StructuredSoftwareDeliberation:
    """Runs bounded, auditable pre-action checks without storing private reasoning."""

    _roles = (
        "architect",
        "implementer",
        "reviewer",
        "tester",
        "integrator_optimizer",
    )
    _role_labels = {
        "architect": "架構師",
        "implementer": "實作者",
        "reviewer": "審查者",
        "tester": "測試者",
        "integrator_optimizer": "整合與優化者",
    }
    _role_actions = {
        "architect": frozenset(
            {
                "reference.research",
                "software.specify",
                "architecture.design",
                "architecture.plan",
            }
        ),
        "implementer": frozenset(
            {
                "software.implement",
                "software.microtask",
                "software.enhance",
            }
        ),
        "reviewer": frozenset({"quality.review"}),
        "tester": frozenset({"software.validate"}),
        "integrator_optimizer": frozenset(
            {
                "workspace.prepare",
                "quality.improve",
                "learning.distill",
                "artifact.package",
                "github.publish",
                "mission.submit",
            }
        ),
    }

    def __init__(self, brain: BrainProvider, *, max_rounds: int = 5) -> None:
        self.brain = brain
        self.max_rounds = min(5, max(1, max_rounds))

    @property
    def pipeline(self) -> list[dict[str, Any]]:
        return [
            {
                "role": role,
                "label": self._role_labels[role],
                "execution": "sequential",
            }
            for role in self._roles
        ]

    async def deliberate(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
        *,
        observations: list[dict[str, Any]],
        use_model: bool = True,
    ) -> DeliberationResult:
        role = self.role_for(step.action_kind)
        role_label = self._role_labels[role]
        if not use_model:
            return self._fallback(mission, step, role, rounds=0)

        previous_summaries: list[dict[str, Any]] = []
        last_model = "deterministic-structured-fallback.v1"
        for round_number in range(1, self.max_rounds + 1):
            try:
                async with self.brain.resource_slot(4):
                    response = await self.brain.chat(
                        self._messages(
                            mission,
                            step,
                            role=role,
                            role_label=role_label,
                            round_number=round_number,
                            observations=observations,
                            previous_summaries=previous_summaries,
                        ),
                        format_schema=self._schema(),
                        options={"temperature": 0.1, "num_predict": 480, "think": False},
                    )
                last_model = response.model
                candidate = self._json_object(response.content)
            except (json.JSONDecodeError, RuntimeError, ValueError):
                continue

            normalized = self._normalize(candidate)
            if normalized is None:
                continue
            previous_summaries.append(normalized)
            if normalized["ready_for_action"]:
                return DeliberationResult(
                    role=role,
                    role_label=role_label,
                    summary=self._render_summary(
                        normalized,
                        role_label=role_label,
                        round_number=round_number,
                    ),
                    rounds=round_number,
                    ready_for_action=True,
                    model=last_model,
                )

        fallback = self._fallback(
            mission,
            step,
            role,
            rounds=self.max_rounds,
        )
        return DeliberationResult(
            role=fallback.role,
            role_label=fallback.role_label,
            summary=fallback.summary,
            rounds=fallback.rounds,
            ready_for_action=False,
            model=last_model,
        )

    @classmethod
    def role_for(cls, action_kind: str) -> str:
        for role, action_kinds in cls._role_actions.items():
            if action_kind in action_kinds:
                return role
        return "integrator_optimizer"

    def _messages(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
        *,
        role: str,
        role_label: str,
        round_number: int,
        observations: list[dict[str, Any]],
        previous_summaries: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "/no_think\n你是 ECK 的持久化任務控制器，現在擔任軟體代理「"
                    f"{role_label}」。不得輸出或儲存私有思考鏈，只能輸出 JSON 結構化決策摘要。"
                    "先檢查能力邊界、可用工具、外部觀察與成功條件；不得宣稱尚未驗證的結果。"
                    "若仍有阻塞，列出 blocking_issues 並將 ready_for_action 設為 false；"
                    "若已有安全且可驗證的下一步，設為 true。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mission": mission.objective,
                        "requirements": mission.completion_requirements,
                        "step": step.objective,
                        "action_kind": step.action_kind,
                        "attempt": step.attempts,
                        "agent_role": role,
                        "deliberation_round": round_number,
                        "maximum_rounds": self.max_rounds,
                        "previous_observations": observations,
                        "prior_structured_summaries": previous_summaries[-2:],
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason_summary": {"type": "string"},
                "unknowns": {"type": "array", "items": {"type": "string"}},
                "tool": {"type": "string"},
                "success_check": {"type": "string"},
                "blocking_issues": {"type": "array", "items": {"type": "string"}},
                "ready_for_action": {"type": "boolean"},
                "recommendation": {"type": "string"},
            },
            "required": ["reason_summary", "unknowns", "tool", "success_check"],
        }

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise
            value = json.loads(content[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("The deliberation response must be a JSON object.")
        return value

    @staticmethod
    def _strings(value: Any, *, limit: int = 5) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:500] for item in value if str(item).strip()][:limit]

    @classmethod
    def _normalize(cls, value: dict[str, Any]) -> dict[str, Any] | None:
        reason_summary = str(value.get("reason_summary", "")).strip()[:1600]
        tool = str(value.get("tool", "")).strip()[:200]
        success_check = str(value.get("success_check", "")).strip()[:1000]
        if not reason_summary or not tool or not success_check:
            return None
        blocking_issues = cls._strings(value.get("blocking_issues"))
        explicit_ready = value.get("ready_for_action")
        ready_for_action = (
            bool(explicit_ready)
            if isinstance(explicit_ready, bool)
            else not blocking_issues
        )
        return {
            "reason_summary": reason_summary,
            "unknowns": cls._strings(value.get("unknowns"), limit=3),
            "tool": tool,
            "success_check": success_check,
            "blocking_issues": blocking_issues,
            "ready_for_action": ready_for_action and not blocking_issues,
            "recommendation": str(value.get("recommendation", "")).strip()[:800],
        }

    @staticmethod
    def _render_summary(
        value: dict[str, Any],
        *,
        role_label: str,
        round_number: int,
    ) -> str:
        parts = [
            f"角色：{role_label}",
            f"摘要：{value['reason_summary']}",
            f"工具：{value['tool']}",
            f"成功條件：{value['success_check']}",
            f"審議輪次：{round_number}/5",
        ]
        if value["unknowns"]:
            parts.insert(2, f"未知項：{'、'.join(value['unknowns'])}")
        if value["blocking_issues"]:
            parts.append(f"阻塞：{'、'.join(value['blocking_issues'])}")
        if value["recommendation"]:
            parts.append(f"建議：{value['recommendation']}")
        return "｜".join(parts)[:4000]

    def _fallback(
        self,
        mission: MissionRecord,
        step: MissionStepRecord,
        role: str,
        *,
        rounds: int,
    ) -> DeliberationResult:
        del mission
        role_label = self._role_labels[role]
        summary = (
            f"角色：{role_label}｜摘要：依持久化計畫執行「{step.objective}」，"
            f"只接受工具 {step.action_kind} 的外部觀察與固定驗證結果｜"
            f"工具：{step.action_kind}｜成功條件：步驟輸出通過既定驗證器｜"
            f"審議輪次：{rounds}/5"
        )
        return DeliberationResult(
            role=role,
            role_label=role_label,
            summary=summary[:4000],
            rounds=rounds,
            ready_for_action=True,
            model="deterministic-structured-fallback.v1",
        )
