from __future__ import annotations

from typing import Any

from eck.app import Application
from eck.domain.enums import (
    ComparisonOperator,
    EvidenceSource,
    RiskLevel,
)
from eck.domain.models import (
    ActionProposal,
    SuccessContract,
    TaskCreate,
    TaskRecord,
    VerificationCheck,
)


class DemoService:
    def __init__(self, application: Application) -> None:
        self.application = application

    async def persistence(self) -> dict[str, Any]:
        await self.application.kernel.run_sleep_cycle()
        state = self.application.store.get_kernel_state(
            self.application.settings.identity
        )
        chain_valid, failed_sequence = self.application.store.verify_event_chain()
        return {
            "identity": self.application.settings.identity,
            "boot_count": state["boot_count"] if state else 0,
            "database_exists": self.application.settings.database_path.exists()
            if self.application.settings.database_path
            else False,
            "event_chain_valid": chain_valid,
            "failed_sequence": failed_sequence,
            "event_count": self.application.store.count_events(),
            "acceptance": bool(state and chain_valid),
            "note": "The automated test suite performs a real stop/rebuild/start recovery test.",
        }

    async def safe_code(self) -> dict[str, Any]:
        contract = SuccessContract(
            goal="Create and verify f(x) = x² + 1 without file or shell access.",
            checks=(
                VerificationCheck(
                    name="All deterministic unit tests pass",
                    path="metrics.all_passed",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
                VerificationCheck(
                    name="No failed test remains",
                    path="metrics.failed",
                    operator=ComparisonOperator.EQ,
                    expected=0,
                ),
            ),
            required_evidence=(
                EvidenceSource.UNIT_TEST,
                EvidenceSource.FORMAL_CHECK,
            ),
            minimum_score=1,
            max_attempts=2,
            max_cost_units=20,
            require_reproducible=True,
        )
        action = ActionProposal(
            capability="python.safe_expression",
            operation="evaluate",
            payload={
                "expression": "x * x + 1",
                "cases": [
                    {"input": -3, "expected": 10},
                    {"input": 0, "expected": 1},
                    {"input": 2, "expected": 5},
                    {"input": 11, "expected": 122},
                ],
            },
            declared_risk=RiskLevel.LOW,
            reversible=True,
            estimated_cost_units=4,
        )
        task = await self.application.tasks.submit(
            TaskCreate(goal=contract.goal, success_contract=contract, action=action)
        )
        task = await self.application.tasks.execute(task.task_id)
        return task.model_dump(mode="json")

    async def gridworld(self) -> dict[str, Any]:
        grid = [
            "S...#...",
            ".##.#.#.",
            "...#...#",
            "#....#..",
            ".###...G",
        ]
        first = await self._grid_task("surface-a", grid)
        second = await self._grid_task("surface-b", grid)
        first_steps = (
            first.result.output["metrics"]["exploration_steps"] if first.result else None
        )
        second_steps = (
            second.result.output["metrics"]["exploration_steps"] if second.result else None
        )
        return {
            "first_attempt": first.model_dump(mode="json"),
            "second_attempt": second.model_dump(mode="json"),
            "learning_measure": {
                "first_exploration_steps": first_steps,
                "second_exploration_steps": second_steps,
                "fewer_steps_after_experience": (
                    isinstance(first_steps, int)
                    and isinstance(second_steps, int)
                    and second_steps < first_steps
                ),
                "scope": (
                    "v0.1 verifies persistent route reuse under a changed surface label. "
                    "It does not claim abstract task generalization."
                ),
            },
        }

    async def all(self) -> dict[str, Any]:
        return {
            "persistence": await self.persistence(),
            "safe_code": await self.safe_code(),
            "gridworld": await self.gridworld(),
        }

    async def _grid_task(
        self, surface_variant: str, grid: list[str]
    ) -> TaskRecord:
        contract = SuccessContract(
            goal="Explore the reversible GridWorld and reach its externally observed goal.",
            checks=(
                VerificationCheck(
                    name="Environment goal reached",
                    path="reached_goal",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
                VerificationCheck(
                    name="No collision occurred",
                    path="metrics.collisions",
                    operator=ComparisonOperator.EQ,
                    expected=0,
                ),
            ),
            required_evidence=(EvidenceSource.ENVIRONMENT,),
            minimum_score=1,
            max_attempts=2,
            max_cost_units=100,
            require_reproducible=True,
        )
        action = ActionProposal(
            capability="gridworld.navigate",
            operation="explore_and_navigate",
            payload={
                "environment_id": "acceptance-maze-001",
                "surface_variant": surface_variant,
                "grid": grid,
            },
            declared_risk=RiskLevel.LOW,
            reversible=True,
            estimated_cost_units=50,
        )
        task = await self.application.tasks.submit(
            TaskCreate(
                goal=contract.goal,
                success_contract=contract,
                action=action,
                labels=("acceptance", "gridworld", surface_variant),
            )
        )
        return await self.application.tasks.execute(task.task_id)
