from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any


class EvolutionProtectedSurfacePolicy:
    """Deterministic mutation boundaries that do not depend on an LLM verdict."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        configured = self.project_root / "config" / "evolution-protected-paths.json"
        bundled = Path(__file__).resolve().parents[3] / "config" / "evolution-protected-paths.json"
        self.path = configured if configured.is_file() else bundled

    def classify(self, paths: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
        policy = self._load()
        immutable = {self._normalize(item) for item in policy["immutable_paths"]}
        owner_prefixes = tuple(
            self._normalize(item).rstrip("/") + "/"
            for item in policy["owner_approval_prefixes"]
        )
        rows: list[dict[str, str]] = []
        for raw in paths:
            path = self._normalize(raw)
            if path in immutable:
                category = "immutable_recovery_boundary"
            elif any(path == prefix[:-1] or path.startswith(prefix) for prefix in owner_prefixes):
                category = "owner_approval_required"
            else:
                category = "ordinary_structural_candidate"
            rows.append({"path": path, "category": category})
        return rows

    def assert_candidate_allowed(self, paths: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
        rows = self.classify(paths)
        blocked = [
            row["path"]
            for row in rows
            if row["category"] == "immutable_recovery_boundary"
        ]
        if blocked:
            raise ValueError(
                "Core candidates cannot modify immutable recovery boundaries: "
                + ", ".join(blocked)
            )
        return rows

    def status(self) -> dict[str, Any]:
        value = self._load()
        return {
            "schema_version": value["schema_version"],
            "policy_path": str(self.path),
            "immutable_count": len(value["immutable_paths"]),
            "owner_approval_prefix_count": len(value["owner_approval_prefixes"]),
        }

    def _load(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Evolution protected-path policy must be an object.")
        immutable = value.get("immutable_paths")
        owner_prefixes = value.get("owner_approval_prefixes")
        if not isinstance(immutable, list) or not isinstance(owner_prefixes, list):
            raise ValueError("Evolution protected-path policy lists are missing.")
        return value

    @staticmethod
    def _normalize(value: object) -> str:
        path = PurePosixPath(str(value).replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe protected-path entry: {value}")
        return path.as_posix().casefold()
