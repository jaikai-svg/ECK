from __future__ import annotations

from collections import deque
from typing import Any

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence
from eck.storage.sqlite import SQLiteStore

Position = tuple[int, int]


class GridWorldCapability(Capability):
    definition = CapabilityDefinition(
        name="gridworld.navigate",
        description="Explore a deterministic hidden-rule grid and reach its goal.",
        default_risk=RiskLevel.LOW,
        deterministic=True,
    )

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        environment_id = str(action.payload["environment_id"])
        grid = self._validate_grid(action.payload["grid"])
        start, goal = self._find_markers(grid)
        fingerprint = f"gridworld.navigate:{environment_id}"
        prior = self.store.get_skill(fingerprint)

        used_prior = False
        expanded = 0
        path: list[Position] | None = None
        if prior:
            candidate = [tuple(item) for item in prior.procedure.get("path", [])]
            if self._path_is_valid(grid, start, goal, candidate):
                path = candidate
                used_prior = True

        if path is None:
            path, expanded = self._breadth_first_search(grid, start, goal)

        reached = path is not None and path[-1] == goal
        actions = self._positions_to_actions(path or [])
        output: dict[str, Any] = {
            "environment_id": environment_id,
            "reached_goal": reached,
            "used_prior_skill": used_prior,
            "metrics": {
                "action_steps": len(actions),
                "exploration_steps": len(actions) if used_prior else expanded,
                "collisions": 0,
            },
            "path": [list(item) for item in path or []],
            "actions": actions,
            "skill_fingerprint": fingerprint,
            "skill_name": f"GridWorld route: {environment_id}",
            "skill_procedure": {"path": [list(item) for item in path or []]},
        }
        evidence = (
            Evidence(
                source=EvidenceSource.ENVIRONMENT,
                claim="The environment reported that the goal state was reached."
                if reached
                else "The environment goal state was not reached.",
                payload={
                    "environment_id": environment_id,
                    "final_position": list(path[-1]) if path else None,
                    "goal": list(goal),
                    "reached": reached,
                },
            ),
        )
        finished = utc_now()
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=reached,
            output=output,
            evidence=evidence,
            reversible=True,
            cost_units=float((expanded or len(actions)) + 1),
            started_at=started,
            finished_at=finished,
        )

    @staticmethod
    def _validate_grid(raw: Any) -> list[str]:
        if not isinstance(raw, list) or not raw or not all(isinstance(row, str) for row in raw):
            raise ValueError("grid must be a non-empty list of strings.")
        width = len(raw[0])
        if width == 0 or any(len(row) != width for row in raw):
            raise ValueError("grid rows must have equal non-zero width.")
        allowed = {".", "#", "S", "G"}
        if any(set(row) - allowed for row in raw):
            raise ValueError("grid contains unsupported symbols.")
        return raw

    @staticmethod
    def _find_markers(grid: list[str]) -> tuple[Position, Position]:
        starts: list[Position] = []
        goals: list[Position] = []
        for row_index, row in enumerate(grid):
            for column_index, value in enumerate(row):
                if value == "S":
                    starts.append((row_index, column_index))
                elif value == "G":
                    goals.append((row_index, column_index))
        if len(starts) != 1 or len(goals) != 1:
            raise ValueError("grid must contain exactly one S and one G.")
        return starts[0], goals[0]

    @classmethod
    def _breadth_first_search(
        cls, grid: list[str], start: Position, goal: Position
    ) -> tuple[list[Position] | None, int]:
        queue: deque[Position] = deque([start])
        parents: dict[Position, Position | None] = {start: None}
        expanded = 0
        while queue:
            current = queue.popleft()
            expanded += 1
            if current == goal:
                path: list[Position] = []
                cursor: Position | None = current
                while cursor is not None:
                    path.append(cursor)
                    cursor = parents[cursor]
                path.reverse()
                return path, expanded
            for neighbor in cls._neighbors(grid, current):
                if neighbor not in parents:
                    parents[neighbor] = current
                    queue.append(neighbor)
        return None, expanded

    @staticmethod
    def _neighbors(grid: list[str], position: Position) -> list[Position]:
        row, column = position
        result: list[Position] = []
        for delta_row, delta_column in ((-1, 0), (0, 1), (1, 0), (0, -1)):
            candidate = row + delta_row, column + delta_column
            if (
                0 <= candidate[0] < len(grid)
                and 0 <= candidate[1] < len(grid[0])
                and grid[candidate[0]][candidate[1]] != "#"
            ):
                result.append(candidate)
        return result

    @classmethod
    def _path_is_valid(
        cls,
        grid: list[str],
        start: Position,
        goal: Position,
        path: list[Position],
    ) -> bool:
        if not path or path[0] != start or path[-1] != goal:
            return False
        for current, following in zip(path, path[1:], strict=False):
            if following not in cls._neighbors(grid, current):
                return False
        return True

    @staticmethod
    def _positions_to_actions(path: list[Position]) -> list[str]:
        actions: list[str] = []
        labels = {(-1, 0): "up", (0, 1): "right", (1, 0): "down", (0, -1): "left"}
        for current, following in zip(path, path[1:], strict=False):
            delta = following[0] - current[0], following[1] - current[1]
            actions.append(labels[delta])
        return actions
