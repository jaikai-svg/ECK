from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.domain.models import (
    MissionCreate,
    MissionReactCycleRecord,
    MissionRecord,
    MissionStepDefinition,
    MissionStepRecord,
    MissionUpdate,
)
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class MissionRepositoryMixin(SQLiteRepositoryMixin):
    def create_mission(self, create: MissionCreate) -> MissionRecord:
        mission_id = new_id("mission")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO missions (
                    mission_id, title, objective, completion_requirements, source,
                    schedule, priority, target_month, status, progress_json,
                    result_summary, evidence_json, review_feedback, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '', ?, ?)
                """,
                (
                    mission_id,
                    create.title,
                    create.objective,
                    create.completion_requirements,
                    create.source,
                    create.schedule,
                    create.priority,
                    create.target_month,
                    MissionStatus.ACTIVE.value,
                    _json(
                        {
                            "completion_percent": 0,
                            "current_step": "等待規劃",
                            "execution_kind": create.execution_kind,
                        }
                    ),
                    _json(()),
                    now,
                    now,
                ),
            )
        return self.get_mission(mission_id)

    def update_mission(
        self,
        mission_id: str,
        update: MissionUpdate,
        *,
        actor: str = "user",
    ) -> MissionRecord:
        editable = (
            "title",
            "objective",
            "completion_requirements",
            "priority",
            "target_month",
        )
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(f"Unknown mission: {mission_id}")
            before = self._mission_edit_snapshot(row)
            after = dict(before)
            changed_fields: list[str] = []
            for field in editable:
                if field not in update.model_fields_set:
                    continue
                value = getattr(update, field)
                if value is None and field != "target_month":
                    continue
                if value != before[field]:
                    after[field] = value
                    changed_fields.append(field)
            if not changed_fields:
                conn.execute("ROLLBACK")
                raise ValueError("No project fields changed.")
            conn.execute(
                """
                UPDATE missions
                SET title = ?, objective = ?, completion_requirements = ?,
                    priority = ?, target_month = ?, updated_at = ?
                WHERE mission_id = ?
                """,
                (
                    after["title"],
                    after["objective"],
                    after["completion_requirements"],
                    after["priority"],
                    after["target_month"],
                    now,
                    mission_id,
                ),
            )
            revision = self._next_mission_revision(conn, mission_id)
            conn.execute(
                """
                INSERT INTO mission_revisions (
                    revision_id, mission_id, revision, before_json, after_json,
                    changed_fields_json, reason, actor, rollback_of_revision_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    new_id("missionrev"),
                    mission_id,
                    revision,
                    _json(before),
                    _json(after),
                    _json(changed_fields),
                    update.edit_reason,
                    actor,
                    now,
                ),
            )
            conn.execute("COMMIT")
        return self.get_mission(mission_id)

    def list_mission_revisions(self, mission_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mission_revisions
                WHERE mission_id = ? ORDER BY revision DESC
                """,
                (mission_id,),
            ).fetchall()
        return [self._mission_revision_from_row(row) for row in rows]

    def rollback_mission_revision(
        self,
        mission_id: str,
        revision_id: str,
        *,
        reason: str,
        actor: str = "user",
    ) -> MissionRecord:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            mission_row = conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
            revision_row = conn.execute(
                """
                SELECT * FROM mission_revisions
                WHERE revision_id = ? AND mission_id = ?
                """,
                (revision_id, mission_id),
            ).fetchone()
            if mission_row is None or revision_row is None:
                conn.execute("ROLLBACK")
                raise KeyError("Unknown project or revision.")
            before = self._mission_edit_snapshot(mission_row)
            after = _load(str(revision_row["before_json"]))
            if before == after:
                conn.execute("ROLLBACK")
                raise ValueError("The project already matches that revision.")
            changed_fields = [key for key in before if before[key] != after[key]]
            conn.execute(
                """
                UPDATE missions
                SET title = ?, objective = ?, completion_requirements = ?,
                    priority = ?, target_month = ?, updated_at = ?
                WHERE mission_id = ?
                """,
                (
                    after["title"],
                    after["objective"],
                    after["completion_requirements"],
                    after["priority"],
                    after["target_month"],
                    now,
                    mission_id,
                ),
            )
            revision = self._next_mission_revision(conn, mission_id)
            conn.execute(
                """
                INSERT INTO mission_revisions (
                    revision_id, mission_id, revision, before_json, after_json,
                    changed_fields_json, reason, actor, rollback_of_revision_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("missionrev"),
                    mission_id,
                    revision,
                    _json(before),
                    _json(after),
                    _json(changed_fields),
                    reason,
                    actor,
                    revision_id,
                    now,
                ),
            )
            conn.execute("COMMIT")
        return self.get_mission(mission_id)

    @staticmethod
    def _mission_edit_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "title": str(row["title"]),
            "objective": str(row["objective"]),
            "completion_requirements": str(row["completion_requirements"]),
            "priority": str(row["priority"]),
            "target_month": row["target_month"],
        }

    @staticmethod
    def _next_mission_revision(conn: sqlite3.Connection, mission_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 AS revision "
            "FROM mission_revisions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        return int(row["revision"])

    @staticmethod
    def _mission_revision_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["before"] = _load(value.pop("before_json"))
        value["after"] = _load(value.pop("after_json"))
        value["changed_fields"] = _load(value.pop("changed_fields_json"))
        return value

    def set_mission_status(
        self,
        mission_id: str,
        status: MissionStatus,
        *,
        progress: dict[str, Any] | None = None,
        result_summary: str | None = None,
        evidence: tuple[str, ...] | None = None,
        review_feedback: str | None = None,
        submitted_at: datetime | None = None,
        approved_at: datetime | None = None,
    ) -> MissionRecord:
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status.value, iso_now()]
        optional = {
            "progress_json": _json(progress) if progress is not None else None,
            "result_summary": result_summary,
            "evidence_json": _json(evidence) if evidence is not None else None,
            "review_feedback": review_feedback,
            "submitted_at": submitted_at.isoformat() if submitted_at else None,
            "approved_at": approved_at.isoformat() if approved_at else None,
        }
        for field, value in optional.items():
            if value is not None:
                assignments.append(f"{field} = ?")
                values.append(value)
        values.append(mission_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE missions SET {', '.join(assignments)} WHERE mission_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown mission: {mission_id}")
        return self.get_mission(mission_id)

    def get_mission(self, mission_id: str) -> MissionRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown mission: {mission_id}")
        return self._mission_from_row(row)

    def list_missions(self, limit: int = 100) -> list[MissionRecord]:
        return self.list_missions_page(limit=limit, offset=0)

    def list_missions_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        statuses: tuple[MissionStatus, ...] | None = None,
    ) -> list[MissionRecord]:
        where = ""
        values: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where = f"WHERE status IN ({placeholders})"
            values.extend(item.value for item in statuses)
        values.extend((limit, offset))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM missions
                {where}
                ORDER BY CASE status WHEN 'approved' THEN 1 ELSE 0 END,
                         CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,
                         created_at DESC LIMIT ? OFFSET ?
                """,
                values,
            ).fetchall()
        return [self._mission_from_row(row) for row in rows]

    def count_missions(
        self,
        *,
        statuses: tuple[MissionStatus, ...] | None = None,
    ) -> int:
        if not statuses:
            return self._count_table("missions")
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM missions WHERE status IN ({placeholders})",
                tuple(item.value for item in statuses),
            ).fetchone()
        return int(row["count"] if row else 0)

    def mission_sequence(self, mission_id: str) -> int:
        with self._connect() as conn:
            target = conn.execute(
                "SELECT created_at FROM missions WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            if not target:
                raise KeyError(f"Unknown mission: {mission_id}")
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM missions
                WHERE created_at < ? OR (created_at = ? AND mission_id <= ?)
                """,
                (target["created_at"], target["created_at"], mission_id),
            ).fetchone()
        return int(row["count"] if row else 0)

    def create_mission_steps(
        self,
        mission_id: str,
        definitions: tuple[MissionStepDefinition, ...],
    ) -> list[MissionStepRecord]:
        if not definitions:
            raise ValueError("A durable mission requires at least one step.")
        keys = {item.step_key for item in definitions}
        if len(keys) != len(definitions):
            raise ValueError("Mission step keys must be unique.")
        unknown_dependencies = {
            dependency
            for item in definitions
            for dependency in item.depends_on
            if dependency not in keys
        }
        if unknown_dependencies:
            raise ValueError(
                "Mission step dependencies are undefined: "
                + ", ".join(sorted(unknown_dependencies))
            )
        now = iso_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) AS count FROM mission_steps WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            if existing and int(existing["count"]) > 0:
                return self.list_mission_steps(mission_id)
            for definition in sorted(definitions, key=lambda item: item.sequence):
                conn.execute(
                    """
                    INSERT INTO mission_steps (
                        step_id, mission_id, step_key, sequence, action_kind, objective,
                        depends_on_json, status, attempts, max_attempts, inputs_json,
                        output_json, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        new_id("mstep"),
                        mission_id,
                        definition.step_key,
                        definition.sequence,
                        definition.action_kind,
                        definition.objective,
                        _json(definition.depends_on),
                        MissionStepStatus.PENDING.value,
                        definition.max_attempts,
                        _json(definition.inputs),
                        _json({}),
                        now,
                        now,
                    ),
                )
        return self.list_mission_steps(mission_id)

    def append_mission_steps(
        self,
        mission_id: str,
        definitions: tuple[MissionStepDefinition, ...],
    ) -> list[MissionStepRecord]:
        if not definitions:
            return self.list_mission_steps(mission_id)
        existing = self.list_mission_steps(mission_id)
        existing_keys = {item.step_key for item in existing}
        new_keys = {item.step_key for item in definitions}
        if len(new_keys) != len(definitions) or existing_keys & new_keys:
            raise ValueError("Appended mission step keys must be new and unique.")
        available = existing_keys | new_keys
        unknown_dependencies = {
            dependency
            for item in definitions
            for dependency in item.depends_on
            if dependency not in available
        }
        if unknown_dependencies:
            raise ValueError(
                "Mission step dependencies are undefined: "
                + ", ".join(sorted(unknown_dependencies))
            )
        now = iso_now()
        with self._connect() as conn:
            for definition in sorted(definitions, key=lambda item: item.sequence):
                conn.execute(
                    """
                    INSERT INTO mission_steps (
                        step_id, mission_id, step_key, sequence, action_kind, objective,
                        depends_on_json, status, attempts, max_attempts, inputs_json,
                        output_json, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        new_id("mstep"),
                        mission_id,
                        definition.step_key,
                        definition.sequence,
                        definition.action_kind,
                        definition.objective,
                        _json(definition.depends_on),
                        MissionStepStatus.PENDING.value,
                        definition.max_attempts,
                        _json(definition.inputs),
                        _json({}),
                        now,
                        now,
                    ),
                )
        return self.list_mission_steps(mission_id)

    def reset_mission_steps_from_sequence(
        self,
        mission_id: str,
        *,
        sequence: int,
        reason: str,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mission_steps
                SET status = ?, attempts = 0, output_json = ?, last_error = ?,
                    started_at = NULL, finished_at = NULL, updated_at = ?
                WHERE mission_id = ? AND sequence >= ?
                """,
                (
                    MissionStepStatus.PENDING.value,
                    _json({}),
                    reason[:8000],
                    iso_now(),
                    mission_id,
                    sequence,
                ),
            )
        return cursor.rowcount

    def get_mission_step(self, step_id: str) -> MissionStepRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mission_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown mission step: {step_id}")
        return self._mission_step_from_row(row)

    def list_mission_steps(self, mission_id: str) -> list[MissionStepRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mission_steps
                WHERE mission_id = ? ORDER BY sequence, created_at
                """,
                (mission_id,),
            ).fetchall()
        return [self._mission_step_from_row(row) for row in rows]

    def claim_next_mission_step(self) -> MissionStepRecord | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT step.* FROM mission_steps AS step
                    JOIN missions AS mission ON mission.mission_id = step.mission_id
                    WHERE step.status = ? AND mission.status IN (?, ?)
                    ORDER BY CASE mission.priority WHEN 'urgent' THEN 0 ELSE 1 END,
                             mission.created_at, step.sequence
                    """,
                    (
                        MissionStepStatus.PENDING.value,
                        MissionStatus.ACTIVE.value,
                        MissionStatus.PREPARING.value,
                    ),
                ).fetchall()
                selected: sqlite3.Row | None = None
                for row in rows:
                    dependencies = tuple(_load(row["depends_on_json"], []))
                    if not dependencies:
                        selected = row
                        break
                    placeholders = ",".join("?" for _ in dependencies)
                    dependency_rows = conn.execute(
                        f"""
                        SELECT step_key, status FROM mission_steps
                        WHERE mission_id = ? AND step_key IN ({placeholders})
                        """,
                        (row["mission_id"], *dependencies),
                    ).fetchall()
                    statuses = {item["step_key"]: item["status"] for item in dependency_rows}
                    if all(
                        statuses.get(dependency) == MissionStepStatus.SUCCEEDED.value
                        for dependency in dependencies
                    ):
                        selected = row
                        break
                if selected is None:
                    conn.execute("COMMIT")
                    return None
                now = iso_now()
                cursor = conn.execute(
                    """
                    UPDATE mission_steps
                    SET status = ?, attempts = attempts + 1, started_at = ?, updated_at = ?
                    WHERE step_id = ? AND status = ?
                    """,
                    (
                        MissionStepStatus.RUNNING.value,
                        now,
                        now,
                        selected["step_id"],
                        MissionStepStatus.PENDING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return self.get_mission_step(str(selected["step_id"]))

    def finish_mission_step(
        self,
        step_id: str,
        *,
        success: bool,
        output: dict[str, Any],
        error: str = "",
        retryable: bool = False,
    ) -> MissionStepRecord:
        current = self.get_mission_step(step_id)
        if current.status is not MissionStepStatus.RUNNING:
            raise ValueError("Only a running mission step can be finished.")
        retry = retryable and current.attempts < current.max_attempts
        status = (
            MissionStepStatus.SUCCEEDED
            if success
            else MissionStepStatus.PENDING
            if retry
            else MissionStepStatus.FAILED
        )
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mission_steps
                SET status = ?, output_json = ?, last_error = ?, updated_at = ?,
                    finished_at = ?
                WHERE step_id = ?
                """,
                (
                    status.value,
                    _json(output),
                    error[:8000],
                    now,
                    now if status is not MissionStepStatus.PENDING else None,
                    step_id,
                ),
            )
        return self.get_mission_step(step_id)

    def block_pending_mission_steps(self, mission_id: str, *, reason: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mission_steps
                SET status = ?, last_error = ?, updated_at = ?, finished_at = ?
                WHERE mission_id = ? AND status = ?
                """,
                (
                    MissionStepStatus.BLOCKED.value,
                    reason[:8000],
                    iso_now(),
                    iso_now(),
                    mission_id,
                    MissionStepStatus.PENDING.value,
                ),
            )
        return cursor.rowcount

    def recover_running_mission_steps(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mission_steps
                SET status = ?, last_error = ?, updated_at = ?, started_at = NULL
                WHERE status = ?
                """,
                (
                    MissionStepStatus.PENDING.value,
                    "Interrupted by kernel restart; safely queued for replay.",
                    iso_now(),
                    MissionStepStatus.RUNNING.value,
                ),
            )
            conn.execute(
                """
                UPDATE mission_react_cycles
                SET status = ?, correction = ?, updated_at = ?, completed_at = ?
                WHERE status = ?
                """,
                (
                    MissionCycleStatus.NEEDS_CORRECTION.value,
                    "Kernel restart interrupted the action; replay the idempotent step.",
                    iso_now(),
                    iso_now(),
                    MissionCycleStatus.RUNNING.value,
                ),
            )
        return cursor.rowcount

    def create_mission_react_cycle(
        self,
        step: MissionStepRecord,
        *,
        reason_summary: str,
        action: dict[str, Any],
    ) -> MissionReactCycleRecord:
        cycle_id = new_id("mcycle")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mission_react_cycles (
                    cycle_id, mission_id, step_id, attempt, reason_summary, action_json,
                    observation_json, correction, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    cycle_id,
                    step.mission_id,
                    step.step_id,
                    step.attempts,
                    reason_summary[:8000],
                    _json(action),
                    _json({}),
                    MissionCycleStatus.RUNNING.value,
                    now,
                    now,
                ),
            )
        return self.get_mission_react_cycle(cycle_id)

    def finish_mission_react_cycle(
        self,
        cycle_id: str,
        *,
        status: MissionCycleStatus,
        observation: dict[str, Any],
        correction: str = "",
    ) -> MissionReactCycleRecord:
        if status is MissionCycleStatus.RUNNING:
            raise ValueError("A completed ReAct cycle cannot remain running.")
        now = iso_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mission_react_cycles
                SET status = ?, observation_json = ?, correction = ?,
                    updated_at = ?, completed_at = ? WHERE cycle_id = ?
                """,
                (status.value, _json(observation), correction[:8000], now, now, cycle_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown mission ReAct cycle: {cycle_id}")
        return self.get_mission_react_cycle(cycle_id)

    def get_mission_react_cycle(self, cycle_id: str) -> MissionReactCycleRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mission_react_cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown mission ReAct cycle: {cycle_id}")
        return self._mission_cycle_from_row(row)

    def list_mission_react_cycles(
        self,
        mission_id: str,
        *,
        limit: int = 100,
    ) -> list[MissionReactCycleRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mission_react_cycles WHERE mission_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (mission_id, limit),
            ).fetchall()
        return [self._mission_cycle_from_row(row) for row in rows]

    @staticmethod
    def _mission_from_row(row: sqlite3.Row) -> MissionRecord:
        return MissionRecord(
            mission_id=row["mission_id"],
            title=row["title"],
            objective=row["objective"],
            completion_requirements=row["completion_requirements"],
            source=row["source"],
            schedule=row["schedule"],
            priority=row["priority"],
            target_month=row["target_month"],
            status=MissionStatus(row["status"]),
            progress=_load(row["progress_json"], {}),
            result_summary=row["result_summary"],
            evidence=tuple(_load(row["evidence_json"], [])),
            review_feedback=row["review_feedback"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            submitted_at=(
                datetime.fromisoformat(row["submitted_at"]) if row["submitted_at"] else None
            ),
            approved_at=(
                datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None
            ),
        )

    @staticmethod
    def _mission_step_from_row(row: sqlite3.Row) -> MissionStepRecord:
        return MissionStepRecord(
            step_id=row["step_id"],
            mission_id=row["mission_id"],
            step_key=row["step_key"],
            sequence=int(row["sequence"]),
            action_kind=row["action_kind"],
            objective=row["objective"],
            depends_on=tuple(_load(row["depends_on_json"], [])),
            status=MissionStepStatus(row["status"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            inputs=_load(row["inputs_json"], {}),
            output=_load(row["output_json"], {}),
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
        )

    @staticmethod
    def _mission_cycle_from_row(row: sqlite3.Row) -> MissionReactCycleRecord:
        return MissionReactCycleRecord(
            cycle_id=row["cycle_id"],
            mission_id=row["mission_id"],
            step_id=row["step_id"],
            attempt=int(row["attempt"]),
            reason_summary=row["reason_summary"],
            action=_load(row["action_json"], {}),
            observation=_load(row["observation_json"], {}),
            correction=row["correction"],
            status=MissionCycleStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
        )
