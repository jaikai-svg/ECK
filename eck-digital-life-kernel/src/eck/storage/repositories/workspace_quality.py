from __future__ import annotations

import sqlite3
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class WorkspaceQualityRepositoryMixin(SQLiteRepositoryMixin):
    """Durable quality-audit records kept separate from Phase 2 projections."""

    def create_sleep_run(self, *, trigger_kind: str) -> dict[str, Any]:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT * FROM sleep_runs
                WHERE status IN ('queued', 'running')
                ORDER BY requested_at DESC LIMIT 1
                """
            ).fetchone()
            if active is not None:
                conn.execute("COMMIT")
                return self._sleep_run_from_row(active)
            run_id = new_id("sleep")
            conn.execute(
                """
                INSERT INTO sleep_runs (
                    run_id, trigger_kind, status, phase, before_json, after_json,
                    changes_json, result_json, error, requested_at, started_at,
                    completed_at, updated_at
                ) VALUES (?, ?, 'queued', 'queued', '{}', '{}', '{}', '{}', '', ?,
                          NULL, NULL, ?)
                """,
                (run_id, trigger_kind, now, now),
            )
            row = conn.execute(
                "SELECT * FROM sleep_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            conn.execute("COMMIT")
        assert row is not None
        return self._sleep_run_from_row(row)

    def recover_sleep_runs(self) -> dict[str, Any] | None:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE sleep_runs
                SET status = 'failed', phase = 'failed',
                    error = 'Interrupted by kernel restart', completed_at = ?,
                    updated_at = ?
                WHERE status = 'running'
                """,
                (now, now),
            )
            queued = conn.execute(
                """
                SELECT * FROM sleep_runs WHERE status = 'queued'
                ORDER BY requested_at, run_id LIMIT 1
                """
            ).fetchone()
            conn.execute("COMMIT")
        return self._sleep_run_from_row(queued) if queued is not None else None

    def update_sleep_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        json_fields = {"before", "after", "changes", "result"}
        allowed = {
            "status",
            "phase",
            "before",
            "after",
            "changes",
            "result",
            "error",
            "started_at",
            "completed_at",
        }
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported sleep-run field: {key}")
            column = f"{key}_json" if key in json_fields else key
            assignments.append(f"{column} = ?")
            values.append(_json(value) if key in json_fields else value)
        values.append(run_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE sleep_runs SET {', '.join(assignments)} WHERE run_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown sleep run: {run_id}")
        return self.get_sleep_run(run_id)

    def get_sleep_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sleep_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown sleep run: {run_id}")
        return self._sleep_run_from_row(row)

    def latest_sleep_run(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sleep_runs ORDER BY requested_at DESC LIMIT 1"
            ).fetchone()
        return self._sleep_run_from_row(row) if row is not None else None

    def list_all_artifacts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_index ORDER BY created_at DESC"
            ).fetchall()
        return [self._quality_artifact_from_row(row) for row in rows]

    def create_artifact_deletion_run(self, record: dict[str, Any]) -> dict[str, Any]:
        deletion_id = str(record.get("deletion_id") or new_id("purge"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifact_deletion_runs (
                    deletion_id, artifact_id, artifact_title, plan_sha256,
                    status, artifact_ids_json, targets_json, deleted_bytes,
                    result_json, error, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, '{}', '', ?, NULL)
                """,
                (
                    deletion_id,
                    record["artifact_id"],
                    record["artifact_title"],
                    record["plan_sha256"],
                    record.get("status", "moving"),
                    _json(record.get("artifact_ids", [])),
                    _json(record.get("targets", [])),
                    iso_now(),
                ),
            )
        return self.get_artifact_deletion_run(deletion_id)

    def finish_artifact_deletion(
        self,
        deletion_id: str,
        *,
        artifact_ids: list[str],
        deleted_bytes: int,
        result: dict[str, Any],
        error: str = "",
    ) -> dict[str, Any]:
        if not artifact_ids:
            raise ValueError("Artifact deletion requires at least one artifact id.")
        completed_at = iso_now()
        status = "failed" if error else "completed"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not error:
                usage_rows = conn.execute(
                    "SELECT usage_id, artifact_ids_json FROM task_skill_usages"
                ).fetchall()
                removed = set(artifact_ids)
                for row in usage_rows:
                    linked = [
                        item
                        for item in _load(row["artifact_ids_json"])
                        if str(item) not in removed
                    ]
                    conn.execute(
                        "UPDATE task_skill_usages SET artifact_ids_json = ? "
                        "WHERE usage_id = ?",
                        (_json(linked), row["usage_id"]),
                    )
                placeholders = ",".join("?" for _ in artifact_ids)
                conn.execute(
                    f"DELETE FROM artifact_cache_entries "
                    f"WHERE artifact_id IN ({placeholders})",
                    artifact_ids,
                )
                conn.execute(
                    f"DELETE FROM archive_records WHERE artifact_id IN ({placeholders})",
                    artifact_ids,
                )
                conn.execute(
                    f"DELETE FROM artifact_index WHERE artifact_id IN ({placeholders})",
                    artifact_ids,
                )
            conn.execute(
                """
                UPDATE artifact_deletion_runs
                SET status = ?, deleted_bytes = ?, result_json = ?, error = ?,
                    completed_at = ? WHERE deletion_id = ?
                """,
                (
                    status,
                    deleted_bytes,
                    _json(result),
                    error,
                    completed_at,
                    deletion_id,
                ),
            )
            conn.execute("COMMIT")
        return self.get_artifact_deletion_run(deletion_id)

    def get_artifact_deletion_run(self, deletion_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_deletion_runs WHERE deletion_id = ?",
                (deletion_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown artifact deletion: {deletion_id}")
        return self._artifact_deletion_from_row(row)

    def update_artifact_deletion_cleanup(
        self, deletion_id: str, cleanup_errors: list[str]
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM artifact_deletion_runs WHERE deletion_id = ?",
                (deletion_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown artifact deletion: {deletion_id}")
            result = _load(row["result_json"])
            result["cleanup_errors"] = cleanup_errors
            cursor = conn.execute(
                """
                UPDATE artifact_deletion_runs
                SET status = 'cleanup_pending', result_json = ?
                WHERE deletion_id = ?
                """,
                (_json(result), deletion_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown artifact deletion: {deletion_id}")
        return self.get_artifact_deletion_run(deletion_id)

    @staticmethod
    def _quality_artifact_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _load(value.pop("metadata_json"))
        return value

    @staticmethod
    def _sleep_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("before", "after", "changes", "result"):
            value[key] = _load(value.pop(f"{key}_json"))
        return value

    @staticmethod
    def _artifact_deletion_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["artifact_ids"] = _load(value.pop("artifact_ids_json"))
        value["targets"] = _load(value.pop("targets_json"))
        value["result"] = _load(value.pop("result_json"))
        return value
