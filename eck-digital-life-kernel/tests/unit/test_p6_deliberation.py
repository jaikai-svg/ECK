from __future__ import annotations

import json
from typing import Any

import pytest

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse
from eck.domain.models import MissionCreate
from eck.experimental.p6.deliberation import StructuredSoftwareDeliberation


class ScriptedDeliberationBrain(BrainProvider):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = 0

    async def health(self) -> BrainHealth:
        return BrainHealth(provider="test", available=True, model="deliberation-test")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        del messages, format_schema, options
        payload = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return BrainResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="deliberation-test",
            raw={},
        )


@pytest.mark.asyncio
async def test_deliberation_stops_when_verifiable_action_is_ready(application) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="設計可靠 API",
            objective="建立具備防禦性設計的高並發 API",
            completion_requirements="通過固定測試與效能基準",
            execution_kind="software_project",
        )
    )
    step = next(
        item
        for item in application.store.list_mission_steps(mission.mission_id)
        if item.action_kind == "architecture.design"
    )
    brain = ScriptedDeliberationBrain(
        [
            {
                "reason_summary": "缺少目前併發限制。",
                "unknowns": ["最大連線數"],
                "tool": "benchmark.inspect",
                "success_check": "取得可重現基準",
                "blocking_issues": ["尚無基準資料"],
                "ready_for_action": False,
            },
            {
                "reason_summary": "先執行有界基準再決定連線池參數。",
                "unknowns": [],
                "tool": "benchmark.inspect",
                "success_check": "基準輸出包含連線數、延遲與記憶體",
                "blocking_issues": [],
                "ready_for_action": True,
            },
        ]
    )
    deliberation = StructuredSoftwareDeliberation(brain, max_rounds=5)

    result = await deliberation.deliberate(mission, step, observations=[])

    assert result.role == "architect"
    assert result.rounds == 2
    assert result.ready_for_action is True
    assert brain.calls == 2
    assert "角色：架構師" in result.summary
    assert "審議輪次：2/5" in result.summary
    assert "思考鏈" not in result.summary


def test_deliberation_exposes_five_serial_roles() -> None:
    deliberation = StructuredSoftwareDeliberation(ScriptedDeliberationBrain([]))

    assert [item["role"] for item in deliberation.pipeline] == [
        "architect",
        "implementer",
        "reviewer",
        "tester",
        "integrator_optimizer",
    ]
    assert {item["execution"] for item in deliberation.pipeline} == {"sequential"}
