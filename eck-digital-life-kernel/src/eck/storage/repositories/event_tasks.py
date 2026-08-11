from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.domain.enums import ApprovalStatus, KernelPhase, RiskLevel, TaskStatus
from eck.domain.models import (
    ActionProposal,
    ApprovalRecord,
    CapabilityResult,
    EventRecord,
    SuccessContract,
    TaskCreate,
    TaskRecord,
    VerificationReport,
)
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import GENESIS_HASH
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class EventTaskRepositoryMixin(SQLiteRepositoryMixin):
    def append_event(
        self,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> EventRecord:
        event_id = new_id("event")
        created_at = iso_now()
        payload_json = _json(payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = row["event_hash"] if row else GENESIS_HASH
            material = "|".join(
                [
                    previous_hash,
                    event_id,
                    event_type,
                    aggregate_id,
                    correlation_id or "",
                    payload_json,
                    created_at,
                ]
            )
            event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            cursor = conn.execute(
                """
                INSERT INTO events (
                    event_id, event_type, aggregate_id, correlation_id,
                    payload_json, previous_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    aggregate_id,
                    correlation_id,
                    payload_json,
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            )
            if cursor.lastrowid is None:
                conn.execute("ROLLBACK")
                raise RuntimeError("SQLite did not return an event sequence.")
            sequence = int(cursor.lastrowid)
            conn.execute("COMMIT")
        return EventRecord(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_events(self, *, after_sequence: int = 0, limit: int = 100) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (after_sequence, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_recent_events(self, *, limit: int = 100) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM events ORDER BY sequence DESC LIMIT ?
                )
                ORDER BY sequence ASC
                """,
                (limit,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def count_events(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()
        return int(row["count"])

    def export_events_jsonl(self) -> str:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY sequence ASC").fetchall()
        lines = []
        for row in rows:
            item = {
                "sequence": row["sequence"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "aggregate_id": row["aggregate_id"],
                "correlation_id": row["correlation_id"],
                "payload_json": row["payload_json"],
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
                "created_at": row["created_at"],
            }
            lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines) + ("\n" if lines else "")

    def verify_event_chain(self) -> tuple[bool, int | None]:
        previous_hash = GENESIS_HASH
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY sequence ASC").fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                self._set_chain_cache(False, int(row["sequence"]), previous_hash)
                return False, int(row["sequence"])
            material = "|".join(
                [
                    previous_hash,
                    row["event_id"],
                    row["event_type"],
                    row["aggregate_id"],
                    row["correlation_id"] or "",
                    row["payload_json"],
                    row["created_at"],
                ]
            )
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if expected != row["event_hash"]:
                self._set_chain_cache(False, int(row["sequence"]), previous_hash)
                return False, int(row["sequence"])
            previous_hash = row["event_hash"]
        sequence = int(rows[-1]["sequence"]) if rows else 0
        self._set_chain_cache(True, sequence, previous_hash)
        return True, None

    def verify_event_chain_incremental(self) -> tuple[bool, int | None]:
        with self._chain_lock:
            if not self._chain_valid:
                return False, self._chain_failed_sequence
            sequence = self._verified_sequence
            previous_hash = self._verified_hash
        if sequence == 0:
            return self.verify_event_chain()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE sequence > ? ORDER BY sequence ASC",
                (sequence,),
            ).fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                failed = int(row["sequence"])
                self._set_chain_cache(False, failed, previous_hash)
                return False, failed
            material = "|".join(
                [
                    previous_hash,
                    row["event_id"],
                    row["event_type"],
                    row["aggregate_id"],
                    row["correlation_id"] or "",
                    row["payload_json"],
                    row["created_at"],
                ]
            )
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if expected != row["event_hash"]:
                failed = int(row["sequence"])
                self._set_chain_cache(False, failed, previous_hash)
                return False, failed
            sequence = int(row["sequence"])
            previous_hash = row["event_hash"]
        self._set_chain_cache(True, sequence, previous_hash)
        return True, None

    def _set_chain_cache(
        self,
        valid: bool,
        sequence: int,
        event_hash: str,
    ) -> None:
        with self._chain_lock:
            self._chain_valid = valid
            self._chain_failed_sequence = None if valid else sequence
            self._verified_sequence = sequence if valid else max(0, sequence - 1)
            self._verified_hash = event_hash

    def begin_boot(self, identity: str) -> tuple[int, bool]:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT boot_count, clean_shutdown FROM kernel_state WHERE identity = ?",
                (identity,),
            ).fetchone()
            recovered = bool(row and not row["clean_shutdown"])
            boot_count = (int(row["boot_count"]) if row else 0) + 1
            conn.execute(
                """
                INSERT INTO kernel_state (
                    identity, phase, boot_count, started_at, last_heartbeat_at,
                    clean_shutdown, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(identity) DO UPDATE SET
                    phase=excluded.phase,
                    boot_count=excluded.boot_count,
                    started_at=excluded.started_at,
                    last_heartbeat_at=excluded.last_heartbeat_at,
                    clean_shutdown=0,
                    updated_at=excluded.updated_at
                """,
                (
                    identity,
                    KernelPhase.STARTING.value,
                    boot_count,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
        return boot_count, recovered

    def update_kernel_state(
        self,
        identity: str,
        phase: KernelPhase,
        *,
        heartbeat: bool = False,
        clean_shutdown: bool | None = None,
    ) -> None:
        now = iso_now()
        assignments = ["phase = ?", "updated_at = ?"]
        values: list[Any] = [phase.value, now]
        if heartbeat:
            assignments.append("last_heartbeat_at = ?")
            values.append(now)
        if clean_shutdown is not None:
            assignments.append("clean_shutdown = ?")
            values.append(int(clean_shutdown))
        values.append(identity)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE kernel_state SET {', '.join(assignments)} WHERE identity = ?",
                values,
            )

    def get_kernel_state(self, identity: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM kernel_state WHERE identity = ?", (identity,)
            ).fetchone()
        return dict(row) if row else None

    def create_task(
        self,
        task_id: str,
        create: TaskCreate,
        risk: RiskLevel,
        *,
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        now = iso_now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, goal, status, risk_level, contract_json, action_json,
                        labels_json, idempotency_key, attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        task_id,
                        create.goal,
                        TaskStatus.QUEUED.value,
                        risk.value,
                        _json(create.success_contract),
                        _json(create.action),
                        _json(create.labels),
                        idempotency_key,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.find_active_task_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing
        return self.get_task(task_id)

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        attempts: int | None = None,
        result: CapabilityResult | None = None,
        verification: VerificationReport | None = None,
    ) -> TaskRecord:
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status.value)
            if status is not TaskStatus.QUEUED:
                assignments.append("next_attempt_at = NULL")
        if attempts is not None:
            assignments.append("attempts = ?")
            values.append(attempts)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(_json(result))
        if verification is not None:
            assignments.append("verification_json = ?")
            values.append(_json(verification))
        values.append(task_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )
        return self.get_task(task_id)

    def find_active_task_by_idempotency_key(
        self,
        idempotency_key: str | None,
    ) -> TaskRecord | None:
        if not idempotency_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE idempotency_key = ?
                  AND status IN ('queued', 'waiting_approval', 'running')
                ORDER BY created_at ASC LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
        return self._task_from_row(row) if row else None

    def schedule_task_retry(
        self,
        task_id: str,
        *,
        next_attempt_at: datetime,
        last_error: str,
    ) -> TaskRecord:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks SET status = ?, next_attempt_at = ?, last_error = ?,
                    result_json = NULL, verification_json = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskStatus.QUEUED.value,
                    next_attempt_at.isoformat(),
                    last_error[:2000],
                    iso_now(),
                    task_id,
                ),
            )
        return self.get_task(task_id)

    def dead_letter_task(self, task_id: str, *, last_error: str) -> TaskRecord:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks SET status = ?, next_attempt_at = NULL, last_error = ?,
                    updated_at = ? WHERE task_id = ?
                """,
                (TaskStatus.BLOCKED.value, last_error[:2000], iso_now(), task_id),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown task: {task_id}")
        return self._task_from_row(row)

    def list_tasks(
        self,
        *,
        statuses: tuple[TaskStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        params: list[Any] = []
        where = ""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where = f"WHERE status IN ({placeholders})"
            params.extend(x.value for x in statuses)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_ready_tasks(self, *, limit: int = 500) -> list[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY created_at ASC LIMIT ?
                """,
                (TaskStatus.QUEUED.value, iso_now(), limit),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_tasks_with_label(
        self,
        label: str,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[TaskRecord]:
        clauses = [
            "EXISTS (SELECT 1 FROM json_each(tasks.labels_json) WHERE value = ?)"
        ]
        params: list[Any] = [label]
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tasks WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def count_tasks(self, statuses: tuple[TaskStatus, ...]) -> int:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM tasks WHERE status IN ({placeholders})",
                tuple(x.value for x in statuses),
            ).fetchone()
        return int(row["count"])

    def create_approval(
        self, task_id: str, action: ActionProposal, reason: str
    ) -> ApprovalRecord:
        approval_id = new_id("approval")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, task_id, action_json, status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    _json(action),
                    ApprovalStatus.PENDING.value,
                    reason,
                    now,
                ),
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown approval: {approval_id}")
        return self._approval_from_row(row)

    def get_task_approval(self, task_id: str) -> ApprovalRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._approval_from_row(row) if row else None

    def decide_approval(
        self, approval_id: str, decision: ApprovalStatus
    ) -> ApprovalRecord:
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("Decision must be approved or rejected.")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE approvals SET status = ?, decided_at = ?
                WHERE approval_id = ? AND status = ?
                """,
                (
                    decision.value,
                    iso_now(),
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError("Approval is not pending.")
        return self.get_approval(approval_id)

    def list_approvals(
        self, status: ApprovalStatus | None = None, limit: int = 100
    ) -> list[ApprovalRecord]:
        where = "WHERE status = ?" if status else ""
        params: tuple[Any, ...] = (status.value, limit) if status else (limit,)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM approvals {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def count_pending_approvals(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM approvals WHERE status = ?",
                (ApprovalStatus.PENDING.value,),
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            sequence=row["sequence"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            aggregate_id=row["aggregate_id"],
            correlation_id=row["correlation_id"],
            payload=_load(row["payload_json"], {}),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            goal=row["goal"],
            status=TaskStatus(row["status"]),
            risk_level=RiskLevel(row["risk_level"]),
            success_contract=SuccessContract.model_validate(_load(row["contract_json"])),
            action=ActionProposal.model_validate(_load(row["action_json"])),
            labels=tuple(_load(row["labels_json"], [])),
            idempotency_key=row["idempotency_key"],
            attempts=row["attempts"],
            next_attempt_at=(
                datetime.fromisoformat(row["next_attempt_at"])
                if row["next_attempt_at"]
                else None
            ),
            last_error=row["last_error"],
            result=(
                CapabilityResult.model_validate(_load(row["result_json"]))
                if row["result_json"]
                else None
            ),
            verification=(
                VerificationReport.model_validate(_load(row["verification_json"]))
                if row["verification_json"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row["approval_id"],
            task_id=row["task_id"],
            action=ActionProposal.model_validate(_load(row["action_json"])),
            status=ApprovalStatus(row["status"]),
            reason=row["reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None
            ),
        )


