from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.domain.enums import (
    ApprovalStatus,
    KernelPhase,
    RiskLevel,
    TaskStatus,
    VerificationStatus,
)
from eck.domain.models import (
    ActionProposal,
    ApprovalRecord,
    CapabilityResult,
    EventRecord,
    ExperienceRecord,
    KnowledgeRecord,
    ReflectionRecord,
    SkillRecord,
    SuccessContract,
    TaskCreate,
    TaskRecord,
    VerificationReport,
)

GENESIS_HASH = "0" * 64


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


class SQLiteStore:
    """Small, explicit persistence layer with a tamper-evident event chain."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    correlation_id TEXT,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_aggregate ON events(aggregate_id);

                CREATE TABLE IF NOT EXISTS kernel_state (
                    identity TEXT PRIMARY KEY,
                    phase TEXT NOT NULL,
                    boot_count INTEGER NOT NULL,
                    started_at TEXT,
                    last_heartbeat_at TEXT,
                    clean_shutdown INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    verification_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    action_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    capability TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    admitted INTEGER NOT NULL,
                    admission_reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_experiences_capability
                    ON experiences(capability);

                CREATE TABLE IF NOT EXISTS knowledge_items (
                    knowledge_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    capability TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    externally_grounded INTEGER NOT NULL,
                    reproducible INTEGER NOT NULL,
                    admitted INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_capability
                    ON knowledge_items(capability);

                CREATE TABLE IF NOT EXISTS reflections (
                    reflection_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    capability TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    observation TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    next_step TEXT NOT NULL,
                    verification_report_id TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    generator TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_reflections_capability
                    ON reflections(capability);

                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    procedure_json TEXT NOT NULL,
                    verification_basis_json TEXT NOT NULL,
                    success_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skills_capability ON skills(capability);

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

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
                return False, int(row["sequence"])
            previous_hash = row["event_hash"]
        return True, None

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

    def create_task(self, task_id: str, create: TaskCreate, risk: RiskLevel) -> TaskRecord:
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, goal, status, risk_level, contract_json, action_json,
                    labels_json, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    task_id,
                    create.goal,
                    TaskStatus.QUEUED.value,
                    risk.value,
                    _json(create.success_contract),
                    _json(create.action),
                    _json(create.labels),
                    now,
                    now,
                ),
            )
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

    def add_experience(
        self,
        *,
        task_id: str,
        capability: str,
        outcome: VerificationStatus,
        summary: str,
        evidence_ids: tuple[str, ...],
        admitted: bool,
        admission_reason: str,
    ) -> ExperienceRecord:
        experience_id = new_id("experience")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO experiences (
                    experience_id, task_id, capability, outcome, summary,
                    evidence_ids_json, admitted, admission_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experience_id,
                    task_id,
                    capability,
                    outcome.value,
                    summary,
                    _json(evidence_ids),
                    int(admitted),
                    admission_reason,
                    created_at,
                ),
            )
        return ExperienceRecord(
            experience_id=experience_id,
            task_id=task_id,
            capability=capability,
            outcome=outcome,
            summary=summary,
            evidence_ids=evidence_ids,
            admitted=admitted,
            admission_reason=admission_reason,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_experiences(self, limit: int = 100) -> list[ExperienceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM experiences ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ExperienceRecord(
                experience_id=row["experience_id"],
                task_id=row["task_id"],
                capability=row["capability"],
                outcome=VerificationStatus(row["outcome"]),
                summary=row["summary"],
                evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
                admitted=bool(row["admitted"]),
                admission_reason=row["admission_reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def add_knowledge(
        self,
        *,
        task_id: str,
        capability: str,
        claim: str,
        outcome: VerificationStatus,
        evidence_ids: tuple[str, ...],
        externally_grounded: bool,
        reproducible: bool,
        admitted: bool,
    ) -> KnowledgeRecord:
        knowledge_id = new_id("knowledge")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_items (
                    knowledge_id, task_id, capability, claim, outcome,
                    evidence_ids_json, externally_grounded, reproducible,
                    admitted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    knowledge_id,
                    task_id,
                    capability,
                    claim,
                    outcome.value,
                    _json(evidence_ids),
                    int(externally_grounded),
                    int(reproducible),
                    int(admitted),
                    created_at,
                ),
            )
        return KnowledgeRecord(
            knowledge_id=knowledge_id,
            task_id=task_id,
            capability=capability,
            claim=claim,
            outcome=outcome,
            evidence_ids=evidence_ids,
            externally_grounded=externally_grounded,
            reproducible=reproducible,
            admitted=admitted,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_knowledge(self, limit: int = 100) -> list[KnowledgeRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            KnowledgeRecord(
                knowledge_id=row["knowledge_id"],
                task_id=row["task_id"],
                capability=row["capability"],
                claim=row["claim"],
                outcome=VerificationStatus(row["outcome"]),
                evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
                externally_grounded=bool(row["externally_grounded"]),
                reproducible=bool(row["reproducible"]),
                admitted=bool(row["admitted"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def add_reflection(
        self,
        *,
        task_id: str,
        capability: str,
        outcome: VerificationStatus,
        observation: str,
        lesson: str,
        next_step: str,
        verification_report_id: str,
        evidence_ids: tuple[str, ...],
        generator: str = "deterministic-template.v1",
    ) -> ReflectionRecord:
        reflection_id = new_id("reflection")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reflections (
                    reflection_id, task_id, capability, outcome, observation,
                    lesson, next_step, verification_report_id, evidence_ids_json,
                    generator, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reflection_id,
                    task_id,
                    capability,
                    outcome.value,
                    observation,
                    lesson,
                    next_step,
                    verification_report_id,
                    _json(evidence_ids),
                    generator,
                    created_at,
                ),
            )
        return ReflectionRecord(
            reflection_id=reflection_id,
            task_id=task_id,
            capability=capability,
            outcome=outcome,
            observation=observation,
            lesson=lesson,
            next_step=next_step,
            verification_report_id=verification_report_id,
            evidence_ids=evidence_ids,
            generator=generator,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_reflections(self, limit: int = 100) -> list[ReflectionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ReflectionRecord(
                reflection_id=row["reflection_id"],
                task_id=row["task_id"],
                capability=row["capability"],
                outcome=VerificationStatus(row["outcome"]),
                observation=row["observation"],
                lesson=row["lesson"],
                next_step=row["next_step"],
                verification_report_id=row["verification_report_id"],
                evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
                generator=row["generator"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def upsert_skill_success(
        self,
        *,
        fingerprint: str,
        name: str,
        capability: str,
        procedure: dict[str, Any],
        verification_basis: dict[str, Any],
        activation_threshold: int = 2,
    ) -> SkillRecord:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM skills WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if row:
                success_count = int(row["success_count"]) + 1
                conn.execute(
                    """
                    UPDATE skills SET
                        procedure_json = ?, verification_basis_json = ?,
                        success_count = ?, active = ?, updated_at = ?
                    WHERE fingerprint = ?
                    """,
                    (
                        _json(procedure),
                        _json(verification_basis),
                        success_count,
                        int(success_count >= activation_threshold),
                        now,
                        fingerprint,
                    ),
                )
            else:
                success_count = 1
                conn.execute(
                    """
                    INSERT INTO skills (
                        skill_id, fingerprint, name, capability, procedure_json,
                        verification_basis_json, success_count, failure_count,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                    """,
                    (
                        new_id("skill"),
                        fingerprint,
                        name,
                        capability,
                        _json(procedure),
                        _json(verification_basis),
                        int(success_count >= activation_threshold),
                        now,
                        now,
                    ),
                )
            conn.execute("COMMIT")
        skill = self.get_skill(fingerprint)
        assert skill is not None
        return skill

    def get_skill(self, fingerprint: str) -> SkillRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return self._skill_from_row(row) if row else None

    def list_skills(self, limit: int = 100) -> list[SkillRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM skills ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._skill_from_row(row) for row in rows]

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
            attempts=row["attempts"],
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

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            skill_id=row["skill_id"],
            fingerprint=row["fingerprint"],
            name=row["name"],
            capability=row["capability"],
            procedure=_load(row["procedure_json"], {}),
            verification_basis=_load(row["verification_basis_json"], {}),
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
