from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eck.core.time import utc_now

GATE_NAMES = (
    "license",
    "security_scan",
    "docker_test",
    "objective_benchmark",
    "local_reproduction",
)


def gates_complete(gates: object) -> bool:
    return isinstance(gates, dict) and all(
        isinstance(gates.get(name), dict) and gates[name].get("passed") is True
        for name in GATE_NAMES
    )


class ToolCampaignStateStore:
    def __init__(self, path: Path, *, target_count: int) -> None:
        self.path = path
        self.target_count = target_count

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._default()
        if not isinstance(value, dict) or value.get("schema_version") != (
            "eck-tool-acquisition-campaign.v1"
        ):
            return self._default()
        candidates = value.get("candidates")
        if not isinstance(candidates, list):
            value["candidates"] = []
        value["target_count"] = self.target_count
        value.setdefault("search_cursor", 0)
        value.setdefault("last_run", None)
        value.setdefault("last_publish", None)
        return value

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def upsert_candidate(
        self,
        state: dict[str, Any],
        candidate: dict[str, Any],
    ) -> None:
        candidates = state.setdefault("candidates", [])
        candidate_id = str(candidate["candidate_id"])
        for index, existing in enumerate(candidates):
            if str(existing.get("candidate_id")) == candidate_id:
                candidates[index] = candidate
                break
        else:
            candidates.append(candidate)
        state["candidates"] = candidates[-500:]

    def summary(self, state: dict[str, Any]) -> dict[str, Any]:
        candidates = [item for item in state.get("candidates", []) if isinstance(item, dict)]
        accepted = [
            item
            for item in candidates
            if item.get("status") == "accepted" and gates_complete(item.get("gates"))
        ]
        return {
            "schema_version": state.get("schema_version"),
            "status": "complete" if len(accepted) >= self.target_count else "active",
            "target_count": self.target_count,
            "accepted_count": len(accepted),
            "remaining_count": max(0, self.target_count - len(accepted)),
            "candidate_count": len(candidates),
            "rejected_count": sum(item.get("status") == "rejected" for item in candidates),
            "last_run": state.get("last_run"),
            "last_publish": state.get("last_publish"),
            "accepted": accepted[-100:],
            "recent_candidates": candidates[-20:],
        }

    def _default(self) -> dict[str, Any]:
        now = utc_now().isoformat()
        return {
            "schema_version": "eck-tool-acquisition-campaign.v1",
            "objective": (
                "Adapt 100 useful public GitHub tools into locally reproduced ECK capabilities."
            ),
            "target_count": self.target_count,
            "search_cursor": 0,
            "candidates": [],
            "last_run": None,
            "last_publish": None,
            "created_at": now,
            "updated_at": now,
        }
