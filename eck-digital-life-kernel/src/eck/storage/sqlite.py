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
    ChallengeStatus,
    KernelPhase,
    MissionStatus,
    RiskLevel,
    RuntimeSkillStatus,
    TaskStatus,
    VerificationStatus,
)
from eck.domain.models import (
    ActionProposal,
    ApprovalRecord,
    AutonomyPolicy,
    BenchmarkRunCreate,
    BenchmarkRunRecord,
    CapabilityResult,
    ChallengeDraftCreate,
    ChallengeDraftRecord,
    ChallengeProgress,
    ChallengeRecord,
    EventRecord,
    ExperienceRecord,
    KnowledgeRecord,
    MissionCreate,
    MissionRecord,
    MissionUpdate,
    ReflectionRecord,
    RuntimeSkillManifest,
    RuntimeSkillRecord,
    RuntimeVersionRecord,
    SkillRecord,
    SocialEngagementContract,
    SocialPostObservation,
    SocialPostObservationCreate,
    SuccessContract,
    SupervisorReviewRecord,
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
                CREATE INDEX IF NOT EXISTS idx_experiences_admitted
                    ON experiences(admitted);

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

                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    strategy_json TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    selected_platform TEXT,
                    next_action TEXT NOT NULL,
                    blocked_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_challenges_status ON challenges(status);
                CREATE INDEX IF NOT EXISTS idx_challenges_kind ON challenges(kind);

                CREATE TABLE IF NOT EXISTS social_post_observations (
                    observation_id TEXT PRIMARY KEY,
                    challenge_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    post_url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    total_comments INTEGER NOT NULL,
                    human_verified_comments INTEGER NOT NULL,
                    likes INTEGER NOT NULL,
                    disclosure_present INTEGER NOT NULL,
                    policy_compliant INTEGER NOT NULL,
                    human_reviewed INTEGER NOT NULL,
                    within_window INTEGER NOT NULL,
                    cadence_compliant INTEGER NOT NULL,
                    contract_satisfied INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE INDEX IF NOT EXISTS idx_social_observations_challenge
                    ON social_post_observations(challenge_id, published_at);

                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    suite TEXT NOT NULL,
                    benchmark_version TEXT NOT NULL,
                    model TEXT NOT NULL,
                    model_artifact_hash TEXT,
                    evaluator TEXT NOT NULL,
                    score REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    protocol_json TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_benchmark_runs_suite
                    ON benchmark_runs(suite, created_at);

                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    completion_requirements TEXT NOT NULL,
                    source TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    target_month TEXT,
                    status TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    review_feedback TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT,
                    approved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_missions_status
                    ON missions(status, priority, created_at);

                CREATE TABLE IF NOT EXISTS runtime_skills (
                    runtime_skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_dir TEXT NOT NULL,
                    source TEXT NOT NULL,
                    test_report_json TEXT NOT NULL,
                    improvements_json TEXT NOT NULL,
                    activation_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    activated_at TEXT,
                    UNIQUE(name, version)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_skills_status
                    ON runtime_skills(status, name);

                CREATE TABLE IF NOT EXISTS runtime_version_state (
                    identity TEXT PRIMARY KEY,
                    major INTEGER NOT NULL,
                    minor INTEGER NOT NULL,
                    patch INTEGER NOT NULL,
                    verified_skill_count INTEGER NOT NULL,
                    next_minor_skill_count INTEGER NOT NULL,
                    pending_updates INTEGER NOT NULL,
                    last_reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO runtime_version_state (
                    identity, major, minor, patch, verified_skill_count,
                    next_minor_skill_count, pending_updates, last_reason, updated_at
                ) VALUES (?, 0, 1, 0, 0, 100, 0, ?, ?)
                """,
                ("kernel", "Initial v0.1.0 state", iso_now()),
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

    def count_experiences(self, *, admitted: bool | None = None) -> int:
        where = "" if admitted is None else "WHERE admitted = ?"
        params: tuple[object, ...] = () if admitted is None else (int(admitted),)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM experiences {where}",
                params,
            ).fetchone()
        return int(row["count"])

    def revoke_task_learning(self, task_id: str, reason: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            experience = conn.execute(
                "SELECT admitted FROM experiences WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not experience or not bool(experience["admitted"]):
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                """
                UPDATE experiences
                SET admitted = 0, admission_reason = ?
                WHERE task_id = ?
                """,
                (f"Revoked after relevance audit: {reason}", task_id),
            )
            conn.execute(
                "UPDATE knowledge_items SET admitted = 0 WHERE task_id = ?",
                (task_id,),
            )
            conn.execute("COMMIT")
        return True

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

    def revoke_skill_success(self, fingerprint: str) -> SkillRecord | None:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT success_count, failure_count FROM skills WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None
            success_count = max(0, int(row["success_count"]) - 1)
            conn.execute(
                """
                UPDATE skills
                SET success_count = ?, failure_count = ?, active = ?, updated_at = ?
                WHERE fingerprint = ?
                """,
                (
                    success_count,
                    int(row["failure_count"]) + 1,
                    int(success_count >= 2),
                    now,
                    fingerprint,
                ),
            )
            conn.execute("COMMIT")
        return self.get_skill(fingerprint)

    def create_challenge(
        self,
        *,
        kind: str,
        title: str,
        objective: str,
        contract: SocialEngagementContract,
        policy: AutonomyPolicy,
        next_action: str,
    ) -> ChallengeRecord:
        challenge_id = new_id("challenge")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO challenges (
                    challenge_id, kind, title, objective, status, contract_json,
                    policy_json, strategy_json, progress_json, next_action,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge_id,
                    kind,
                    title,
                    objective,
                    ChallengeStatus.PLANNING.value,
                    _json(contract),
                    _json(policy),
                    _json({}),
                    _json(ChallengeProgress()),
                    next_action,
                    now,
                    now,
                ),
            )
        return self.get_challenge(challenge_id)

    def update_challenge(
        self,
        challenge_id: str,
        *,
        status: ChallengeStatus | None = None,
        strategy: dict[str, Any] | None = None,
        progress: ChallengeProgress | None = None,
        selected_platform: str | None = None,
        next_action: str | None = None,
        blocked_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> ChallengeRecord:
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status.value)
        if strategy is not None:
            assignments.append("strategy_json = ?")
            values.append(_json(strategy))
        if progress is not None:
            assignments.append("progress_json = ?")
            values.append(_json(progress))
        if selected_platform is not None:
            assignments.append("selected_platform = ?")
            values.append(selected_platform)
        if next_action is not None:
            assignments.append("next_action = ?")
            values.append(next_action)
        if blocked_reason is not None:
            assignments.append("blocked_reason = ?")
            values.append(blocked_reason)
        if completed_at is not None:
            assignments.append("completed_at = ?")
            values.append(completed_at.isoformat())
        values.append(challenge_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE challenges SET {', '.join(assignments)} WHERE challenge_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown challenge: {challenge_id}")
        return self.get_challenge(challenge_id)

    def get_challenge(self, challenge_id: str) -> ChallengeRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown challenge: {challenge_id}")
        return self._challenge_from_row(row)

    def list_challenges(self, limit: int = 100) -> list[ChallengeRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM challenges ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._challenge_from_row(row) for row in rows]

    def add_social_post_observation(
        self,
        challenge_id: str,
        create: SocialPostObservationCreate,
        *,
        within_window: bool,
        cadence_compliant: bool,
        contract_satisfied: bool,
    ) -> SocialPostObservation:
        observation_id = new_id("social-observation")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO social_post_observations (
                    observation_id, challenge_id, platform, post_url, published_at,
                    observed_at, total_comments, human_verified_comments, likes,
                    disclosure_present, policy_compliant, human_reviewed,
                    within_window, cadence_compliant, contract_satisfied, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    challenge_id,
                    create.platform,
                    create.post_url,
                    create.published_at.isoformat(),
                    create.observed_at.isoformat(),
                    create.total_comments,
                    create.human_verified_comments,
                    create.likes,
                    int(create.disclosure_present),
                    int(create.policy_compliant),
                    int(create.human_reviewed),
                    int(within_window),
                    int(cadence_compliant),
                    int(contract_satisfied),
                    created_at,
                ),
            )
        return self.get_social_post_observation(observation_id)

    def get_social_post_observation(self, observation_id: str) -> SocialPostObservation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM social_post_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown social observation: {observation_id}")
        return self._social_observation_from_row(row)

    def list_social_post_observations(
        self, challenge_id: str, limit: int = 100
    ) -> list[SocialPostObservation]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM social_post_observations
                WHERE challenge_id = ? ORDER BY published_at DESC LIMIT ?
                """,
                (challenge_id, limit),
            ).fetchall()
        return [self._social_observation_from_row(row) for row in rows]

    def add_benchmark_run(self, create: BenchmarkRunCreate) -> BenchmarkRunRecord:
        run_id = new_id("benchmark")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO benchmark_runs (
                    run_id, suite, benchmark_version, model, model_artifact_hash,
                    evaluator, score, sample_count, protocol_json, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    create.suite.value,
                    create.benchmark_version,
                    create.model,
                    create.model_artifact_hash,
                    create.evaluator,
                    create.score,
                    create.sample_count,
                    _json(create.protocol),
                    create.notes,
                    created_at,
                ),
            )
        return BenchmarkRunRecord(
            run_id=run_id,
            created_at=datetime.fromisoformat(created_at),
            **create.model_dump(),
        )

    def list_benchmark_runs(self, limit: int = 100) -> list[BenchmarkRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM benchmark_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._benchmark_run_from_row(row) for row in rows]

    def add_challenge_draft(self, create: ChallengeDraftCreate) -> ChallengeDraftRecord:
        draft_id = new_id("challenge-draft")
        created_at = iso_now()
        payload = {
            "goal": create.goal,
            "completion_requirements": create.completion_requirements,
            "status": "draft",
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations (observation_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (draft_id, "challenge_draft", _json(payload), created_at),
            )
        return ChallengeDraftRecord(
            draft_id=draft_id,
            goal=create.goal,
            completion_requirements=create.completion_requirements,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_challenge_drafts(self, limit: int = 100) -> list[ChallengeDraftRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM observations
                WHERE kind = ? ORDER BY created_at DESC LIMIT ?
                """,
                ("challenge_draft", limit),
            ).fetchall()
        records = []
        for row in rows:
            payload = _load(row["payload_json"], {})
            records.append(
                ChallengeDraftRecord(
                    draft_id=row["observation_id"],
                    goal=payload["goal"],
                    completion_requirements=payload["completion_requirements"],
                    status=payload.get("status", "draft"),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return records

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
                    _json({"completion_percent": 0, "current_step": "等待規劃"}),
                    _json(()),
                    now,
                    now,
                ),
            )
        return self.get_mission(mission_id)

    def update_mission(self, mission_id: str, update: MissionUpdate) -> MissionRecord:
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        for field in (
            "title",
            "objective",
            "completion_requirements",
            "priority",
            "target_month",
        ):
            value = getattr(update, field)
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
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM missions
                ORDER BY CASE status WHEN 'approved' THEN 1 ELSE 0 END,
                         CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,
                         created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._mission_from_row(row) for row in rows]

    def add_runtime_skill(
        self,
        manifest: RuntimeSkillManifest,
        *,
        source_dir: str,
        source: str,
        status: RuntimeSkillStatus = RuntimeSkillStatus.DRAFT,
        test_report: dict[str, Any] | None = None,
        improvements: tuple[str, ...] = (),
    ) -> RuntimeSkillRecord:
        runtime_skill_id = new_id("runtime-skill")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_skills (
                    runtime_skill_id, name, version, manifest_json, status,
                    source_dir, source, test_report_json, improvements_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runtime_skill_id,
                    manifest.name,
                    manifest.version,
                    _json(manifest),
                    status.value,
                    source_dir,
                    source,
                    _json(test_report or {}),
                    _json(improvements),
                    now,
                    now,
                ),
            )
        return self.get_runtime_skill(runtime_skill_id)

    def update_runtime_skill(
        self,
        runtime_skill_id: str,
        *,
        status: RuntimeSkillStatus,
        test_report: dict[str, Any] | None = None,
        activate: bool = False,
    ) -> RuntimeSkillRecord:
        skill = self.get_runtime_skill(runtime_skill_id)
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if activate:
                conn.execute(
                    """
                    UPDATE runtime_skills SET status = ?, updated_at = ?
                    WHERE name = ? AND runtime_skill_id != ? AND status = ?
                    """,
                    (
                        RuntimeSkillStatus.RETIRED.value,
                        now,
                        skill.manifest.name,
                        runtime_skill_id,
                        RuntimeSkillStatus.ACTIVE.value,
                    ),
                )
            assignments = ["status = ?", "updated_at = ?"]
            values: list[Any] = [status.value, now]
            if test_report is not None:
                assignments.append("test_report_json = ?")
                values.append(_json(test_report))
            if activate:
                assignments.extend(["activated_at = ?", "activation_count = activation_count + 1"])
                values.append(now)
            values.append(runtime_skill_id)
            conn.execute(
                f"UPDATE runtime_skills SET {', '.join(assignments)} WHERE runtime_skill_id = ?",
                values,
            )
            conn.execute("COMMIT")
        return self.get_runtime_skill(runtime_skill_id)

    def get_runtime_skill(self, runtime_skill_id: str) -> RuntimeSkillRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_skills WHERE runtime_skill_id = ?",
                (runtime_skill_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown runtime skill: {runtime_skill_id}")
        return self._runtime_skill_from_row(row)

    def find_active_runtime_skill(self, name: str) -> RuntimeSkillRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runtime_skills
                WHERE name = ? AND status = ? ORDER BY activated_at DESC LIMIT 1
                """,
                (name, RuntimeSkillStatus.ACTIVE.value),
            ).fetchone()
        return self._runtime_skill_from_row(row) if row else None

    def list_runtime_skills(self, limit: int = 200) -> list[RuntimeSkillRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_skills ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._runtime_skill_from_row(row) for row in rows]

    def get_runtime_version(self) -> RuntimeVersionRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_version_state WHERE identity = 'kernel'"
            ).fetchone()
        if not row:
            raise RuntimeError("Runtime version state is unavailable.")
        return RuntimeVersionRecord(
            version=f"{row['major']}.{row['minor']}.{row['patch']}",
            major=row["major"],
            minor=row["minor"],
            patch=row["patch"],
            verified_skill_count=row["verified_skill_count"],
            next_minor_skill_count=row["next_minor_skill_count"],
            pending_updates=row["pending_updates"],
            last_reason=row["last_reason"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def update_runtime_version(
        self,
        *,
        major: int,
        minor: int,
        patch: int,
        verified_skill_count: int,
        next_minor_skill_count: int,
        pending_updates: int,
        reason: str,
    ) -> RuntimeVersionRecord:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runtime_version_state SET
                    major = ?, minor = ?, patch = ?, verified_skill_count = ?,
                    next_minor_skill_count = ?, pending_updates = ?,
                    last_reason = ?, updated_at = ? WHERE identity = 'kernel'
                """,
                (
                    major,
                    minor,
                    patch,
                    verified_skill_count,
                    next_minor_skill_count,
                    pending_updates,
                    reason,
                    iso_now(),
                ),
            )
        return self.get_runtime_version()

    def add_supervisor_review(
        self,
        *,
        model: str,
        mood: str,
        activity_text: str,
        assessment: str,
        recommendations: tuple[str, ...],
        challenge_topic: str | None,
        challenge_goal: str | None,
        task_id: str | None,
    ) -> SupervisorReviewRecord:
        review_id = new_id("supervisor-review")
        created_at = iso_now()
        payload = {
            "model": model,
            "mood": mood,
            "activity_text": activity_text,
            "assessment": assessment,
            "recommendations": list(recommendations),
            "challenge_topic": challenge_topic,
            "challenge_goal": challenge_goal,
            "task_id": task_id,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations (observation_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (review_id, "supervisor_review", _json(payload), created_at),
            )
        return SupervisorReviewRecord.model_validate(
            {
                "review_id": review_id,
                "created_at": created_at,
                **payload,
            }
        )

    def list_supervisor_reviews(self, limit: int = 100) -> list[SupervisorReviewRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM observations
                WHERE kind = ? ORDER BY created_at DESC LIMIT ?
                """,
                ("supervisor_review", limit),
            ).fetchall()
        records = []
        for row in rows:
            payload = _load(row["payload_json"], {})
            records.append(
                SupervisorReviewRecord(
                    review_id=row["observation_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    **payload,
                )
            )
        return records

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
    def _challenge_from_row(row: sqlite3.Row) -> ChallengeRecord:
        return ChallengeRecord(
            challenge_id=row["challenge_id"],
            kind=row["kind"],
            title=row["title"],
            objective=row["objective"],
            status=ChallengeStatus(row["status"]),
            contract=SocialEngagementContract.model_validate(_load(row["contract_json"])),
            policy=AutonomyPolicy.model_validate(_load(row["policy_json"])),
            strategy=_load(row["strategy_json"], {}),
            progress=ChallengeProgress.model_validate(_load(row["progress_json"], {})),
            selected_platform=row["selected_platform"],
            next_action=row["next_action"],
            blocked_reason=row["blocked_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )

    @staticmethod
    def _social_observation_from_row(row: sqlite3.Row) -> SocialPostObservation:
        return SocialPostObservation(
            observation_id=row["observation_id"],
            challenge_id=row["challenge_id"],
            platform=row["platform"],
            post_url=row["post_url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            total_comments=row["total_comments"],
            human_verified_comments=row["human_verified_comments"],
            likes=row["likes"],
            disclosure_present=bool(row["disclosure_present"]),
            policy_compliant=bool(row["policy_compliant"]),
            human_reviewed=bool(row["human_reviewed"]),
            within_window=bool(row["within_window"]),
            cadence_compliant=bool(row["cadence_compliant"]),
            contract_satisfied=bool(row["contract_satisfied"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _benchmark_run_from_row(row: sqlite3.Row) -> BenchmarkRunRecord:
        return BenchmarkRunRecord(
            run_id=row["run_id"],
            suite=row["suite"],
            benchmark_version=row["benchmark_version"],
            model=row["model"],
            model_artifact_hash=row["model_artifact_hash"],
            evaluator=row["evaluator"],
            score=row["score"],
            sample_count=row["sample_count"],
            protocol=_load(row["protocol_json"], {}),
            notes=row["notes"],
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
    def _runtime_skill_from_row(row: sqlite3.Row) -> RuntimeSkillRecord:
        return RuntimeSkillRecord(
            runtime_skill_id=row["runtime_skill_id"],
            manifest=RuntimeSkillManifest.model_validate(_load(row["manifest_json"])),
            status=RuntimeSkillStatus(row["status"]),
            source_dir=row["source_dir"],
            source=row["source"],
            test_report=_load(row["test_report_json"], {}),
            improvements=tuple(_load(row["improvements_json"], [])),
            activation_count=row["activation_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            activated_at=(
                datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None
            ),
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
