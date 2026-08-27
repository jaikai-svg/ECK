from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from eck.core.time import iso_now
from eck.storage.repositories.common import GENESIS_HASH
from eck.storage.repositories.event_tasks import EventTaskRepositoryMixin
from eck.storage.repositories.evolution_transactions import EvolutionTransactionRepositoryMixin
from eck.storage.repositories.learning import LearningRepositoryMixin
from eck.storage.repositories.missions import MissionRepositoryMixin
from eck.storage.repositories.runtime_research import RuntimeResearchRepositoryMixin
from eck.storage.repositories.workspace_phase2 import WorkspacePhase2RepositoryMixin
from eck.storage.repositories.workspace_quality import WorkspaceQualityRepositoryMixin


class SQLiteStore(
    EventTaskRepositoryMixin,
    EvolutionTransactionRepositoryMixin,
    LearningRepositoryMixin,
    MissionRepositoryMixin,
    RuntimeResearchRepositoryMixin,
    WorkspaceQualityRepositoryMixin,
    WorkspacePhase2RepositoryMixin,
):
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

                CREATE TABLE IF NOT EXISTS artifact_index (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    task_id TEXT,
                    project_id TEXT,
                    worker TEXT NOT NULL,
                    model TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    local_path TEXT NOT NULL,
                    storage_state TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    integrity_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_kind, source_id, local_path),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_index_page
                    ON artifact_index(created_at DESC, artifact_id);
                CREATE INDEX IF NOT EXISTS idx_artifact_index_filter
                    ON artifact_index(artifact_type, status, project_id);
                CREATE INDEX IF NOT EXISTS idx_artifact_index_task
                    ON artifact_index(task_id);

                CREATE TABLE IF NOT EXISTS task_skill_usages (
                    usage_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    project_id TEXT,
                    runtime_skill_id TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    input_summary TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    result_status TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    artifact_ids_json TEXT NOT NULL,
                    failure_detail TEXT NOT NULL,
                    retry_result TEXT NOT NULL,
                    rollback_result TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(task_id, runtime_skill_id, operation, attempt),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
                    FOREIGN KEY(runtime_skill_id) REFERENCES runtime_skills(runtime_skill_id)
                );
                CREATE INDEX IF NOT EXISTS idx_task_skill_usages_task
                    ON task_skill_usages(task_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_task_skill_usages_skill
                    ON task_skill_usages(runtime_skill_id, started_at);

                CREATE TABLE IF NOT EXISTS archive_records (
                    archive_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    remove_local INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    restored_at TEXT,
                    FOREIGN KEY(artifact_id) REFERENCES artifact_index(artifact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_archive_records_artifact
                    ON archive_records(artifact_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS artifact_cache_entries (
                    artifact_id TEXT PRIMARY KEY,
                    cache_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    in_use_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(artifact_id) REFERENCES artifact_index(artifact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_cache_lru
                    ON artifact_cache_entries(in_use_count, last_accessed_at);

                CREATE TABLE IF NOT EXISTS library_domains (
                    domain_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    knowledge_selector_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    thresholds_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_domain_cards (
                    domain_id TEXT NOT NULL,
                    knowledge_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(domain_id, knowledge_id),
                    FOREIGN KEY(domain_id) REFERENCES library_domains(domain_id),
                    FOREIGN KEY(knowledge_id) REFERENCES knowledge_items(knowledge_id)
                );

                CREATE TABLE IF NOT EXISTS knowledge_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_knowledge_id TEXT NOT NULL,
                    target_knowledge_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_knowledge_id, target_knowledge_id, relation_type),
                    FOREIGN KEY(source_knowledge_id) REFERENCES knowledge_items(knowledge_id),
                    FOREIGN KEY(target_knowledge_id) REFERENCES knowledge_items(knowledge_id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
                    ON knowledge_relations(source_knowledge_id, relation_type);

                CREATE TABLE IF NOT EXISTS library_readiness_reports (
                    report_id TEXT PRIMARY KEY,
                    domain_id TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    threshold_digest TEXT NOT NULL,
                    thresholds_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    gates_json TEXT NOT NULL,
                    critical_gaps_json TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(domain_id) REFERENCES library_domains(domain_id)
                );
                CREATE INDEX IF NOT EXISTS idx_library_readiness_domain
                    ON library_readiness_reports(domain_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS library_books (
                    book_id TEXT PRIMARY KEY,
                    domain_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(domain_id) REFERENCES library_domains(domain_id)
                );
                CREATE INDEX IF NOT EXISTS idx_library_books_domain
                    ON library_books(domain_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS library_book_revisions (
                    revision_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    readiness_report_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    previous_sha256 TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    diff_summary TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(book_id, revision),
                    UNIQUE(book_id, content_sha256),
                    FOREIGN KEY(book_id) REFERENCES library_books(book_id),
                    FOREIGN KEY(readiness_report_id)
                        REFERENCES library_readiness_reports(report_id)
                );

                CREATE TABLE IF NOT EXISTS library_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    revision_id TEXT,
                    suggestion_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mission_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(book_id) REFERENCES library_books(book_id),
                    FOREIGN KEY(revision_id) REFERENCES library_book_revisions(revision_id),
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                );
                CREATE INDEX IF NOT EXISTS idx_library_suggestions_book
                    ON library_suggestions(book_id, status, created_at DESC);

                CREATE TABLE IF NOT EXISTS mission_revisions (
                    revision_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    rollback_of_revision_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(mission_id, revision),
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id),
                    FOREIGN KEY(rollback_of_revision_id)
                        REFERENCES mission_revisions(revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mission_revisions_mission
                    ON mission_revisions(mission_id, revision DESC);

                CREATE TABLE IF NOT EXISTS sleep_runs (
                    run_id TEXT PRIMARY KEY,
                    trigger_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    changes_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sleep_runs_latest
                    ON sleep_runs(requested_at DESC, run_id);

                CREATE TABLE IF NOT EXISTS artifact_deletion_runs (
                    deletion_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    artifact_title TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_ids_json TEXT NOT NULL,
                    targets_json TEXT NOT NULL,
                    deleted_bytes INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_deletions_artifact
                    ON artifact_deletion_runs(artifact_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS evolution_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    base_tree_sha256 TEXT NOT NULL,
                    candidate_tree_sha TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    protected_paths_json TEXT NOT NULL,
                    fixed_gates_json TEXT NOT NULL,
                    approval_json TEXT NOT NULL,
                    expected_commit_sha TEXT,
                    previous_commit_sha TEXT,
                    rollback_commit_sha TEXT,
                    restart_nonce TEXT,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    activation_requested_at TEXT,
                    restart_verified_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_evolution_transactions_status
                    ON evolution_transactions(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS evolution_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    transaction_id TEXT NOT NULL,
                    pack_id TEXT NOT NULL,
                    pack_sha256 TEXT NOT NULL,
                    baseline_json TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    improvement_score REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(transaction_id)
                        REFERENCES evolution_transactions(transaction_id)
                );
                CREATE INDEX IF NOT EXISTS idx_evolution_evaluations_transaction
                    ON evolution_evaluations(transaction_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS evolution_boot_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    transaction_id TEXT NOT NULL,
                    expected_commit_sha TEXT NOT NULL,
                    observed_commit_sha TEXT NOT NULL,
                    boot_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(transaction_id)
                        REFERENCES evolution_transactions(transaction_id)
                );
                CREATE INDEX IF NOT EXISTS idx_evolution_boot_receipts_transaction
                    ON evolution_boot_receipts(transaction_id, created_at DESC);
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
