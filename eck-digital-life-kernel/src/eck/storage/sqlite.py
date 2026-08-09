from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import zlib
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
    MissionCycleStatus,
    MissionStatus,
    MissionStepStatus,
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
    LearningThemeCreate,
    LearningThemeRecord,
    MissionCreate,
    MissionReactCycleRecord,
    MissionRecord,
    MissionStepDefinition,
    MissionStepRecord,
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
from eck.research.dedup import simhash_distance

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
        self._chain_lock = threading.Lock()
        self._verified_sequence = 0
        self._verified_hash = GENESIS_HASH
        self._chain_valid = True
        self._chain_failed_sequence: int | None = None

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
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

                CREATE TABLE IF NOT EXISTS learning_themes (
                    theme_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_themes_active
                    ON learning_themes(active, updated_at);

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

                CREATE TABLE IF NOT EXISTS mission_steps (
                    step_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    step_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    action_kind TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    inputs_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    last_error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(mission_id, step_key),
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mission_steps_ready
                    ON mission_steps(status, mission_id, sequence);

                CREATE TABLE IF NOT EXISTS mission_react_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    reason_summary TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    correction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id),
                    FOREIGN KEY(step_id) REFERENCES mission_steps(step_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mission_cycles_step
                    ON mission_react_cycles(mission_id, step_id, created_at);

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

                CREATE TABLE IF NOT EXISTS research_contents (
                    content_id TEXT PRIMARY KEY,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    simhash TEXT NOT NULL,
                    text_zlib BLOB,
                    text_chars INTEGER NOT NULL,
                    extraction_method TEXT NOT NULL,
                    retain_until TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_contents_simhash
                    ON research_contents(simhash);
                CREATE INDEX IF NOT EXISTS idx_research_contents_retention
                    ON research_contents(retain_until);

                CREATE TABLE IF NOT EXISTS research_source_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
                    url_sha256 TEXT NOT NULL,
                    raw_sha256 TEXT NOT NULL,
                    content_id TEXT NOT NULL,
                    duplicate_of_content_id TEXT,
                    source_domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT,
                    provider TEXT NOT NULL,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(canonical_url, content_id),
                    FOREIGN KEY(content_id) REFERENCES research_contents(content_id),
                    FOREIGN KEY(duplicate_of_content_id)
                        REFERENCES research_contents(content_id)
                );
                CREATE INDEX IF NOT EXISTS idx_research_snapshots_url
                    ON research_source_snapshots(url_sha256, fetched_at);
                CREATE INDEX IF NOT EXISTS idx_research_snapshots_domain
                    ON research_source_snapshots(source_domain, fetched_at);

                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    seed_url TEXT,
                    status TEXT NOT NULL,
                    conclusion_status TEXT NOT NULL,
                    conclusion TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    queries_json TEXT NOT NULL,
                    source_snapshot_ids_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_research_runs_status
                    ON research_runs(status, finished_at);

                CREATE TABLE IF NOT EXISTS research_claims (
                    claim_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    claim_text TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    rationale TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_research_claims_run
                    ON research_claims(run_id, status);

                CREATE TABLE IF NOT EXISTS research_evidence_links (
                    link_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    stance TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    note TEXT NOT NULL,
                    independence_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(claim_id) REFERENCES research_claims(claim_id),
                    FOREIGN KEY(snapshot_id)
                        REFERENCES research_source_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_research_evidence_claim
                    ON research_evidence_links(claim_id, stance);

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
            self._ensure_column(conn, "tasks", "idempotency_key", "TEXT")
            self._ensure_column(conn, "tasks", "next_attempt_at", "TEXT")
            self._ensure_column(conn, "tasks", "last_error", "TEXT")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_active_idempotency
                ON tasks(idempotency_key)
                WHERE idempotency_key IS NOT NULL
                  AND status IN ('queued', 'waiting_approval', 'running')
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_ready
                ON tasks(status, next_attempt_at, created_at)
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

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
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

    def latest_experience(self, *, admitted: bool = False) -> ExperienceRecord | None:
        where = "WHERE admitted = 1" if admitted else ""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM experiences {where} ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return ExperienceRecord(
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

    def count_knowledge(self) -> int:
        return self._count_table("knowledge_items")

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

    def count_reflections(self) -> int:
        return self._count_table("reflections")

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

    def count_skills(self) -> int:
        return self._count_table("skills")

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

    def count_challenges(self) -> int:
        return self._count_table("challenges")

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

    def count_benchmark_runs(self) -> int:
        return self._count_table("benchmark_runs")

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

    def add_learning_theme(self, create: LearningThemeCreate) -> LearningThemeRecord:
        title = " ".join(create.title.split())
        now = iso_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT theme_id FROM learning_themes WHERE title = ? COLLATE NOCASE",
                (title,),
            ).fetchone()
            if existing:
                theme_id = str(existing["theme_id"])
                conn.execute(
                    "UPDATE learning_themes SET active = 1, updated_at = ? WHERE theme_id = ?",
                    (now, theme_id),
                )
            else:
                theme_id = new_id("learning-theme")
                conn.execute(
                    """
                    INSERT INTO learning_themes (
                        theme_id, title, active, created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?)
                    """,
                    (theme_id, title, now, now),
                )
        return self.get_learning_theme(theme_id)

    def get_learning_theme(self, theme_id: str) -> LearningThemeRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_themes WHERE theme_id = ?", (theme_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown learning theme: {theme_id}")
        return self._learning_theme_from_row(row)

    def list_learning_themes(
        self,
        *,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[LearningThemeRecord]:
        where = "WHERE active = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM learning_themes {where}
                ORDER BY active DESC, updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._learning_theme_from_row(row) for row in rows]

    def set_learning_theme_active(
        self,
        theme_id: str,
        *,
        active: bool,
    ) -> LearningThemeRecord:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE learning_themes SET active = ?, updated_at = ? WHERE theme_id = ?",
                (int(active), iso_now(), theme_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown learning theme: {theme_id}")
        return self.get_learning_theme(theme_id)

    def delete_learning_theme(self, theme_id: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM learning_themes WHERE theme_id = ?", (theme_id,)
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown learning theme: {theme_id}")

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

    def count_missions(self) -> int:
        return self._count_table("missions")

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

    def count_observations(self, kind: str, *, since: datetime | None = None) -> int:
        where = "kind = ?"
        params: list[Any] = [kind]
        if since is not None:
            where += " AND created_at >= ?"
            params.append(since.isoformat())
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM observations WHERE {where}",
                params,
            ).fetchone()
        return int(row["count"])

    def add_research_snapshot(
        self,
        *,
        canonical_url: str,
        url_sha256: str,
        raw_sha256: str,
        content_sha256: str,
        simhash: str,
        text: str,
        extraction_method: str,
        retain_until: datetime,
        source_domain: str,
        title: str,
        author: str | None,
        provider: str,
        published_at: str | None,
        fetched_at: datetime,
        content_type: str,
        metadata: dict[str, Any],
        near_duplicate_distance: int,
    ) -> dict[str, Any]:
        now = iso_now()
        compressed = zlib.compress(text.encode("utf-8"), level=9)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exact = conn.execute(
                "SELECT * FROM research_contents WHERE content_sha256 = ?",
                (content_sha256,),
            ).fetchone()
            duplicate_of_content_id: str | None = None
            exact_duplicate = exact is not None
            near_duplicate = False
            if exact is None:
                candidates = conn.execute(
                    """
                    SELECT content_id, simhash FROM research_contents
                    ORDER BY created_at DESC LIMIT 2000
                    """
                ).fetchall()
                nearest = next(
                    (
                        row
                        for row in candidates
                        if simhash_distance(simhash, str(row["simhash"]))
                        <= near_duplicate_distance
                    ),
                    None,
                )
                if nearest is not None:
                    duplicate_of_content_id = str(nearest["content_id"])
                    near_duplicate = True
                content_id = new_id("research-content")
                conn.execute(
                    """
                    INSERT INTO research_contents (
                        content_id, content_sha256, simhash, text_zlib, text_chars,
                        extraction_method, retain_until, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        content_id,
                        content_sha256,
                        simhash,
                        compressed,
                        len(text),
                        extraction_method,
                        retain_until.isoformat(),
                        now,
                    ),
                )
            else:
                content_id = str(exact["content_id"])
                duplicate_of_content_id = content_id
                if exact["text_zlib"] is None:
                    conn.execute(
                        """
                        UPDATE research_contents SET text_zlib = ?, text_chars = ?,
                            extraction_method = ?, retain_until = ?
                        WHERE content_id = ?
                        """,
                        (
                            compressed,
                            len(text),
                            extraction_method,
                            retain_until.isoformat(),
                            content_id,
                        ),
                    )
                elif str(exact["retain_until"]) < retain_until.isoformat():
                    conn.execute(
                        "UPDATE research_contents SET retain_until = ? WHERE content_id = ?",
                        (retain_until.isoformat(), content_id),
                    )
            existing_snapshot = conn.execute(
                """
                SELECT snapshot_id FROM research_source_snapshots
                WHERE canonical_url = ? AND content_id = ?
                """,
                (canonical_url, content_id),
            ).fetchone()
            if existing_snapshot is None:
                snapshot_id = new_id("research-snapshot")
                conn.execute(
                    """
                    INSERT INTO research_source_snapshots (
                        snapshot_id, canonical_url, url_sha256, raw_sha256,
                        content_id, duplicate_of_content_id, source_domain,
                        title, author, provider, published_at, fetched_at,
                        content_type, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        canonical_url,
                        url_sha256,
                        raw_sha256,
                        content_id,
                        duplicate_of_content_id,
                        source_domain,
                        title,
                        author,
                        provider,
                        published_at,
                        fetched_at.isoformat(),
                        content_type,
                        _json(metadata),
                        now,
                    ),
                )
            else:
                snapshot_id = str(existing_snapshot["snapshot_id"])
            conn.execute("COMMIT")
        return {
            "snapshot_id": snapshot_id,
            "content_id": content_id,
            "canonical_url": canonical_url,
            "content_sha256": content_sha256,
            "simhash": simhash,
            "duplicate_of_content_id": duplicate_of_content_id,
            "exact_duplicate": exact_duplicate,
            "near_duplicate": near_duplicate,
            "independence_key": duplicate_of_content_id or content_id,
            "source_domain": source_domain,
            "title": title,
            "provider": provider,
            "published_at": published_at,
            "fetched_at": fetched_at.isoformat(),
        }

    def begin_research_run(
        self,
        *,
        action_id: str,
        topic: str,
        seed_url: str | None,
    ) -> str:
        run_id = new_id("research-run")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_runs (
                    run_id, action_id, topic, seed_url, status,
                    conclusion_status, conclusion, confidence, queries_json,
                    source_snapshot_ids_json, metrics_json, report_json, started_at
                ) VALUES (?, ?, ?, ?, 'running', 'unverified', '', 0, '[]',
                          '[]', '{}', '{}', ?)
                """,
                (run_id, action_id, topic, seed_url, iso_now()),
            )
        return run_id

    def complete_research_run(
        self,
        run_id: str,
        *,
        status: str,
        conclusion_status: str,
        conclusion: str,
        confidence: float,
        queries: list[str],
        source_snapshot_ids: list[str],
        metrics: dict[str, Any],
        report: dict[str, Any],
        claims: list[dict[str, Any]],
        evidence_links: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE research_runs SET status = ?, conclusion_status = ?,
                    conclusion = ?, confidence = ?, queries_json = ?,
                    source_snapshot_ids_json = ?, metrics_json = ?, report_json = ?,
                    finished_at = ? WHERE run_id = ?
                """,
                (
                    status,
                    conclusion_status,
                    conclusion,
                    confidence,
                    _json(queries),
                    _json(source_snapshot_ids),
                    _json(metrics),
                    _json(report),
                    now,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                conn.execute("ROLLBACK")
                raise KeyError(f"Unknown research run: {run_id}")
            claim_ids: list[str] = []
            for claim in claims:
                claim_id = new_id("research-claim")
                claim_ids.append(claim_id)
                conn.execute(
                    """
                    INSERT INTO research_claims (
                        claim_id, run_id, claim_text, kind, status,
                        confidence, rationale, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        run_id,
                        claim["claim"],
                        claim["kind"],
                        claim["status"],
                        claim["confidence"],
                        claim["rationale"],
                        now,
                    ),
                )
            for link in evidence_links:
                claim_index = int(link["claim_index"])
                if claim_index < 0 or claim_index >= len(claim_ids):
                    continue
                conn.execute(
                    """
                    INSERT INTO research_evidence_links (
                        link_id, claim_id, snapshot_id, stance, excerpt,
                        note, independence_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("research-evidence"),
                        claim_ids[claim_index],
                        link["snapshot_id"],
                        link["stance"],
                        link["excerpt"],
                        link["note"],
                        link["independence_key"],
                        now,
                    ),
                )
            conn.execute("COMMIT")
        return self.get_research_run(run_id)

    def fail_research_run(
        self,
        run_id: str,
        *,
        conclusion: str,
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE research_runs SET status = 'failed',
                    conclusion_status = 'unverified', conclusion = ?,
                    metrics_json = ?, finished_at = ? WHERE run_id = ?
                """,
                (conclusion, _json(metrics), iso_now(), run_id),
            )

    def fail_running_research_runs(self, *, conclusion: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE research_runs SET status = 'failed',
                    conclusion_status = 'unverified', conclusion = ?,
                    metrics_json = ?, finished_at = ?
                WHERE status = 'running'
                """,
                (
                    conclusion,
                    _json(
                        {
                            "research_completed": False,
                            "error_type": "InterruptedResearchRun",
                            "reconciled": True,
                        }
                    ),
                    iso_now(),
                ),
            )
        return max(0, cursor.rowcount)

    def get_research_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown research run: {run_id}")
            claims = conn.execute(
                "SELECT * FROM research_claims WHERE run_id = ? ORDER BY created_at, claim_id",
                (run_id,),
            ).fetchall()
            snapshot_ids = [str(item) for item in _load(row["source_snapshot_ids_json"], [])]
            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                sources = conn.execute(
                    f"""
                    SELECT * FROM research_source_snapshots
                    WHERE snapshot_id IN ({placeholders}) ORDER BY fetched_at
                    """,
                    snapshot_ids,
                ).fetchall()
            else:
                sources = []
            evidence = conn.execute(
                """
                SELECT link.* FROM research_evidence_links AS link
                JOIN research_claims AS claim ON claim.claim_id = link.claim_id
                WHERE claim.run_id = ? ORDER BY link.created_at, link.link_id
                """,
                (run_id,),
            ).fetchall()
        return {
            "run_id": row["run_id"],
            "action_id": row["action_id"],
            "topic": row["topic"],
            "seed_url": row["seed_url"],
            "status": row["status"],
            "conclusion_status": row["conclusion_status"],
            "conclusion": row["conclusion"],
            "confidence": row["confidence"],
            "queries": _load(row["queries_json"], []),
            "source_snapshot_ids": _load(row["source_snapshot_ids_json"], []),
            "metrics": _load(row["metrics_json"], {}),
            "report": _load(row["report_json"], {}),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "claims": [
                {
                    "claim_id": claim["claim_id"],
                    "claim": claim["claim_text"],
                    "kind": claim["kind"],
                    "status": claim["status"],
                    "confidence": claim["confidence"],
                    "rationale": claim["rationale"],
                }
                for claim in claims
            ],
            "sources": [
                {
                    "snapshot_id": source["snapshot_id"],
                    "canonical_url": source["canonical_url"],
                    "content_id": source["content_id"],
                    "duplicate_of_content_id": source["duplicate_of_content_id"],
                    "source_domain": source["source_domain"],
                    "title": source["title"],
                    "provider": source["provider"],
                    "published_at": source["published_at"],
                    "fetched_at": source["fetched_at"],
                }
                for source in sources
            ],
            "evidence_links": [
                {
                    "link_id": link["link_id"],
                    "claim_id": link["claim_id"],
                    "snapshot_id": link["snapshot_id"],
                    "stance": link["stance"],
                    "excerpt": link["excerpt"],
                    "note": link["note"],
                    "independence_key": link["independence_key"],
                }
                for link in evidence
            ],
        }

    def list_research_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id FROM research_runs
                ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self.get_research_run(str(row["run_id"])) for row in rows]

    def research_quality_metrics(
        self,
        *,
        window: int,
        max_inconclusive_ratio: float,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT conclusion_status FROM research_runs
                WHERE status = 'completed'
                ORDER BY finished_at DESC LIMIT ?
                """,
                (window,),
            ).fetchall()
        completed = len(rows)
        inconclusive = sum(
            str(row["conclusion_status"]) == "inconclusive" for row in rows
        )
        ratio = inconclusive / completed if completed else 0.0
        threshold_exceeded = completed >= window and ratio > max_inconclusive_ratio
        return {
            "status": (
                "degraded"
                if threshold_exceeded
                else ("ok" if completed >= window else "insufficient_history")
            ),
            "window": window,
            "completed_runs": completed,
            "inconclusive_runs": inconclusive,
            "inconclusive_ratio": round(ratio, 4),
            "max_inconclusive_ratio": max_inconclusive_ratio,
            "threshold_exceeded": threshold_exceeded,
        }

    def get_research_content_text(self, content_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT text_zlib FROM research_contents WHERE content_id = ?",
                (content_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown research content: {content_id}")
        if row["text_zlib"] is None:
            return None
        return zlib.decompress(row["text_zlib"]).decode("utf-8")

    def purge_expired_research_content(self, *, before: datetime) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE research_contents SET text_zlib = NULL
                WHERE text_zlib IS NOT NULL AND retain_until < ?
                """,
                (before.isoformat(),),
            )
        return max(0, cursor.rowcount)

    def _count_table(self, table: str) -> int:
        allowed = {
            "benchmark_runs",
            "challenges",
            "knowledge_items",
            "missions",
            "reflections",
            "skills",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported count table: {table}")
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
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

    @staticmethod
    def _learning_theme_from_row(row: sqlite3.Row) -> LearningThemeRecord:
        return LearningThemeRecord(
            theme_id=row["theme_id"],
            title=row["title"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
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
