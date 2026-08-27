from __future__ import annotations

import sqlite3
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class WorkspacePhase2RepositoryMixin(SQLiteRepositoryMixin):
    """Supplementary indexes and relations for Workspace Phase 2."""

    def upsert_artifact(self, record: dict[str, Any]) -> dict[str, Any]:
        now = iso_now()
        artifact_id = str(record["artifact_id"])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifact_index (
                    artifact_id, artifact_type, title, status, source_kind,
                    source_id, task_id, project_id, worker, model, version,
                    content_sha256, size_bytes, local_path, storage_state,
                    mime_type, metadata_json, integrity_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    artifact_type = excluded.artifact_type,
                    title = excluded.title,
                    status = excluded.status,
                    task_id = excluded.task_id,
                    project_id = excluded.project_id,
                    worker = excluded.worker,
                    model = excluded.model,
                    version = excluded.version,
                    content_sha256 = excluded.content_sha256,
                    size_bytes = excluded.size_bytes,
                    local_path = excluded.local_path,
                    storage_state = excluded.storage_state,
                    mime_type = excluded.mime_type,
                    metadata_json = excluded.metadata_json,
                    integrity_status = excluded.integrity_status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    artifact_id,
                    record["artifact_type"],
                    record["title"],
                    record.get("status", "available"),
                    record["source_kind"],
                    record["source_id"],
                    record.get("task_id"),
                    record.get("project_id"),
                    record.get("worker", ""),
                    record.get("model", ""),
                    record.get("version", "1"),
                    record["content_sha256"],
                    int(record.get("size_bytes", 0)),
                    record["local_path"],
                    record.get("storage_state", "local"),
                    record.get("mime_type", "application/octet-stream"),
                    _json(record.get("metadata", {})),
                    record.get("integrity_status", "verified"),
                    record.get("created_at") or now,
                    now,
                ),
            )
        return self.get_artifact(artifact_id)

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_index WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        return self._artifact_from_row(row)

    def list_artifacts_page(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        artifact_type: str = "",
        status: str = "",
        storage_state: str = "",
        project_id: str = "",
        skill_id: str = "",
        query: str = "",
        created_from: str = "",
        created_to: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        values: list[Any] = []
        if artifact_type:
            clauses.append("a.artifact_type = ?")
            values.append(artifact_type)
        if status:
            clauses.append("a.status = ?")
            values.append(status)
        if storage_state:
            clauses.append("a.storage_state = ?")
            values.append(storage_state)
        if project_id:
            clauses.append("a.project_id = ?")
            values.append(project_id)
        if created_from:
            clauses.append("date(a.created_at) >= date(?)")
            values.append(created_from)
        if created_to:
            clauses.append("date(a.created_at) <= date(?)")
            values.append(created_to)
        if query:
            clauses.append("(a.title LIKE ? OR a.source_id LIKE ?)")
            match = f"%{query}%"
            values.extend((match, match))
        if skill_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM task_skill_usages u "
                "WHERE u.task_id = a.task_id AND u.runtime_skill_id = ?)"
            )
            values.append(skill_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM artifact_index a {where}", values
            ).fetchone()
            rows = conn.execute(
                f"SELECT a.* FROM artifact_index a {where} "
                "ORDER BY a.created_at DESC, a.artifact_id LIMIT ? OFFSET ?",
                (*values, max(1, min(limit, 200)), max(0, offset)),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows], int(total_row["count"])

    def set_artifact_storage(
        self,
        artifact_id: str,
        *,
        storage_state: str,
        integrity_status: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE artifact_index
                SET storage_state = ?, integrity_status = ?, updated_at = ?
                WHERE artifact_id = ?
                """,
                (storage_state, integrity_status, iso_now(), artifact_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        return self.get_artifact(artifact_id)

    def create_task_skill_usage(self, record: dict[str, Any]) -> dict[str, Any]:
        usage_id = str(record.get("usage_id") or new_id("skilluse"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_skill_usages (
                    usage_id, task_id, project_id, runtime_skill_id, skill_name,
                    skill_version, operation, attempt, input_summary, input_sha256,
                    result_status, verification_status, evidence_ids_json,
                    artifact_ids_json, failure_detail, retry_result, rollback_result,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage_id,
                    record["task_id"],
                    record.get("project_id"),
                    record["runtime_skill_id"],
                    record["skill_name"],
                    record["skill_version"],
                    record["operation"],
                    int(record.get("attempt", 1)),
                    record.get("input_summary", ""),
                    record["input_sha256"],
                    record.get("result_status", "running"),
                    record.get("verification_status", "pending"),
                    _json(record.get("evidence_ids", [])),
                    _json(record.get("artifact_ids", [])),
                    record.get("failure_detail", ""),
                    record.get("retry_result", ""),
                    record.get("rollback_result", ""),
                    record.get("started_at", iso_now()),
                    record.get("finished_at"),
                ),
            )
        return self.get_task_skill_usage(usage_id)

    def finish_task_skill_usage(
        self,
        usage_id: str,
        *,
        result_status: str,
        verification_status: str,
        evidence_ids: list[str] | tuple[str, ...] = (),
        artifact_ids: list[str] | tuple[str, ...] = (),
        failure_detail: str = "",
        retry_result: str = "",
        rollback_result: str = "",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_skill_usages
                SET result_status = ?, verification_status = ?, evidence_ids_json = ?,
                    artifact_ids_json = ?, failure_detail = ?, retry_result = ?,
                    rollback_result = ?, finished_at = ?
                WHERE usage_id = ?
                """,
                (
                    result_status,
                    verification_status,
                    _json(evidence_ids),
                    _json(artifact_ids),
                    failure_detail,
                    retry_result,
                    rollback_result,
                    iso_now(),
                    usage_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown task-skill usage: {usage_id}")
        return self.get_task_skill_usage(usage_id)

    def get_task_skill_usage(self, usage_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_skill_usages WHERE usage_id = ?", (usage_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task-skill usage: {usage_id}")
        return self._usage_from_row(row)

    def list_task_skill_usages(
        self,
        *,
        task_id: str = "",
        runtime_skill_id: str = "",
        project_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("task_id", task_id),
            ("runtime_skill_id", runtime_skill_id),
            ("project_id", project_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM task_skill_usages {where} "
                "ORDER BY started_at DESC LIMIT ?",
                (*values, max(1, min(limit, 500))),
            ).fetchall()
        return [self._usage_from_row(row) for row in rows]

    def attach_artifact_to_task_usages(self, task_id: str, artifact_id: str) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT usage_id, artifact_ids_json FROM task_skill_usages WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            updated = 0
            for row in rows:
                artifact_ids = list(_load(row["artifact_ids_json"]))
                if artifact_id in artifact_ids:
                    continue
                artifact_ids.append(artifact_id)
                conn.execute(
                    "UPDATE task_skill_usages SET artifact_ids_json = ? WHERE usage_id = ?",
                    (_json(artifact_ids), row["usage_id"]),
                )
                updated += 1
        return updated

    def create_archive_record(self, record: dict[str, Any]) -> dict[str, Any]:
        archive_id = str(record.get("archive_id") or new_id("archive"))
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO archive_records (
                    archive_id, artifact_id, provider, source_path, archive_path,
                    manifest_json, status, content_sha256, file_count, size_bytes,
                    remove_local, error, created_at, updated_at, restored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    archive_id,
                    record["artifact_id"],
                    record.get("provider", "filesystem"),
                    record["source_path"],
                    record.get("archive_path", ""),
                    _json(record.get("manifest", {})),
                    record.get("status", "preparing"),
                    record.get("content_sha256", ""),
                    int(record.get("file_count", 0)),
                    int(record.get("size_bytes", 0)),
                    int(bool(record.get("remove_local", False))),
                    record.get("error", ""),
                    now,
                    now,
                    record.get("restored_at"),
                ),
            )
        return self.get_archive_record(archive_id)

    def update_archive_record(self, archive_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "archive_path",
            "manifest_json",
            "status",
            "content_sha256",
            "file_count",
            "size_bytes",
            "error",
            "restored_at",
        }
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        for key, value in changes.items():
            column = "manifest_json" if key == "manifest" else key
            if column not in allowed:
                raise ValueError(f"Unsupported archive field: {key}")
            assignments.append(f"{column} = ?")
            values.append(_json(value) if column == "manifest_json" else value)
        values.append(archive_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE archive_records SET {', '.join(assignments)} "
                "WHERE archive_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown archive record: {archive_id}")
        return self.get_archive_record(archive_id)

    def get_archive_record(self, archive_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM archive_records WHERE archive_id = ?", (archive_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown archive record: {archive_id}")
        return self._archive_from_row(row)

    def latest_archive_for_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM archive_records WHERE artifact_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (artifact_id,),
            ).fetchone()
        return self._archive_from_row(row) if row is not None else None

    def list_archives_for_artifact(self, artifact_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM archive_records WHERE artifact_id = ?
                ORDER BY created_at DESC
                """,
                (artifact_id,),
            ).fetchall()
        return [self._archive_from_row(row) for row in rows]

    def upsert_cache_entry(self, record: dict[str, Any]) -> dict[str, Any]:
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifact_cache_entries (
                    artifact_id, cache_path, content_sha256, size_bytes,
                    last_accessed_at, in_use_count, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    cache_path = excluded.cache_path,
                    content_sha256 = excluded.content_sha256,
                    size_bytes = excluded.size_bytes,
                    last_accessed_at = excluded.last_accessed_at,
                    in_use_count = excluded.in_use_count,
                    status = excluded.status
                """,
                (
                    record["artifact_id"],
                    record["cache_path"],
                    record["content_sha256"],
                    int(record.get("size_bytes", 0)),
                    record.get("last_accessed_at", now),
                    int(record.get("in_use_count", 0)),
                    record.get("status", "ready"),
                    record.get("created_at", now),
                ),
            )
        entry = self.get_cache_entry(str(record["artifact_id"]))
        assert entry is not None
        return entry

    def get_cache_entry(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_cache_entries WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_cache_entries(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_cache_entries ORDER BY last_accessed_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def change_cache_use(self, artifact_id: str, delta: int) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE artifact_cache_entries
                SET in_use_count = MAX(0, in_use_count + ?), last_accessed_at = ?
                WHERE artifact_id = ?
                """,
                (delta, iso_now(), artifact_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown cache entry: {artifact_id}")
        entry = self.get_cache_entry(artifact_id)
        assert entry is not None
        return entry

    def delete_cache_entry(self, artifact_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM artifact_cache_entries WHERE artifact_id = ?",
                (artifact_id,),
            )

    def create_library_domain(self, record: dict[str, Any]) -> dict[str, Any]:
        domain_id = str(record.get("domain_id") or new_id("domain"))
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO library_domains (
                    domain_id, slug, title, description, knowledge_selector_json,
                    status, thresholds_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    domain_id,
                    record["slug"],
                    record["title"],
                    record.get("description", ""),
                    _json(record.get("knowledge_selector", {})),
                    record.get("status", "exploring"),
                    _json(record.get("thresholds", {})),
                    now,
                    now,
                ),
            )
        return self.get_library_domain(domain_id)

    def get_library_domain(self, domain_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_domains WHERE domain_id = ?", (domain_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown library domain: {domain_id}")
        return self._domain_from_row(row)

    def list_library_domains(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM library_domains ORDER BY updated_at DESC"
            ).fetchall()
        return [self._domain_from_row(row) for row in rows]

    def update_library_domain(self, domain_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"title", "description", "status", "knowledge_selector", "thresholds"}
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported domain field: {key}")
            column = f"{key}_json" if key in {"knowledge_selector", "thresholds"} else key
            assignments.append(f"{column} = ?")
            values.append(_json(value) if key in {"knowledge_selector", "thresholds"} else value)
        values.append(domain_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE library_domains SET {', '.join(assignments)} WHERE domain_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown library domain: {domain_id}")
        return self.get_library_domain(domain_id)

    def bind_domain_card(self, domain_id: str, knowledge_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO library_domain_cards (domain_id, knowledge_id, added_at)
                VALUES (?, ?, ?)
                """,
                (domain_id, knowledge_id, iso_now()),
            )

    def replace_domain_cards(self, domain_id: str, knowledge_ids: list[str]) -> None:
        selected = list(dict.fromkeys(knowledge_ids))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if selected:
                placeholders = ",".join("?" for _ in selected)
                conn.execute(
                    f"DELETE FROM library_domain_cards WHERE domain_id = ? "
                    f"AND knowledge_id NOT IN ({placeholders})",
                    (domain_id, *selected),
                )
                now = iso_now()
                conn.executemany(
                    "INSERT OR IGNORE INTO library_domain_cards "
                    "(domain_id, knowledge_id, added_at) VALUES (?, ?, ?)",
                    ((domain_id, knowledge_id, now) for knowledge_id in selected),
                )
            else:
                conn.execute(
                    "DELETE FROM library_domain_cards WHERE domain_id = ?",
                    (domain_id,),
                )
            conn.execute("COMMIT")

    def list_domain_knowledge_ids(self, domain_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT knowledge_id FROM library_domain_cards
                WHERE domain_id = ? ORDER BY added_at, knowledge_id
                """,
                (domain_id,),
            ).fetchall()
        return [str(row["knowledge_id"]) for row in rows]

    def create_knowledge_relation(self, record: dict[str, Any]) -> dict[str, Any]:
        relation_id = str(record.get("relation_id") or new_id("relation"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_relations (
                    relation_id, source_knowledge_id, target_knowledge_id,
                    relation_type, rationale, evidence_ids_json, verified, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_knowledge_id, target_knowledge_id, relation_type)
                DO UPDATE SET rationale = excluded.rationale,
                    evidence_ids_json = excluded.evidence_ids_json,
                    verified = excluded.verified
                """,
                (
                    relation_id,
                    record["source_knowledge_id"],
                    record["target_knowledge_id"],
                    record["relation_type"],
                    record.get("rationale", ""),
                    _json(record.get("evidence_ids", [])),
                    int(bool(record.get("verified", False))),
                    iso_now(),
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM knowledge_relations
                WHERE source_knowledge_id = ? AND target_knowledge_id = ?
                  AND relation_type = ?
                """,
                (
                    record["source_knowledge_id"],
                    record["target_knowledge_id"],
                    record["relation_type"],
                ),
            ).fetchone()
        assert row is not None
        return self._relation_from_row(row)

    def list_knowledge_relations(self, knowledge_ids: list[str]) -> list[dict[str, Any]]:
        if not knowledge_ids:
            return []
        placeholders = ",".join("?" for _ in knowledge_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM knowledge_relations
                WHERE source_knowledge_id IN ({placeholders})
                   OR target_knowledge_id IN ({placeholders})
                ORDER BY created_at
                """,
                (*knowledge_ids, *knowledge_ids),
            ).fetchall()
        return [self._relation_from_row(row) for row in rows]

    def create_readiness_report(self, record: dict[str, Any]) -> dict[str, Any]:
        report_id = str(record.get("report_id") or new_id("readiness"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO library_readiness_reports (
                    report_id, domain_id, source_digest, threshold_digest,
                    thresholds_json, metrics_json, gates_json, critical_gaps_json,
                    passed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    record["domain_id"],
                    record["source_digest"],
                    record["threshold_digest"],
                    _json(record["thresholds"]),
                    _json(record["metrics"]),
                    _json(record["gates"]),
                    _json(record.get("critical_gaps", [])),
                    int(bool(record.get("passed", False))),
                    iso_now(),
                ),
            )
        return self.get_readiness_report(report_id)

    def get_readiness_report(self, report_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_readiness_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown readiness report: {report_id}")
        return self._readiness_from_row(row)

    def latest_readiness_report(self, domain_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM library_readiness_reports WHERE domain_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (domain_id,),
            ).fetchone()
        return self._readiness_from_row(row) if row is not None else None

    def create_or_get_library_book(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM library_books WHERE domain_id = ? ORDER BY created_at LIMIT 1",
                (record["domain_id"],),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            book_id = str(record.get("book_id") or new_id("book"))
            now = iso_now()
            conn.execute(
                """
                INSERT INTO library_books (
                    book_id, domain_id, title, description, status,
                    current_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    book_id,
                    record["domain_id"],
                    record["title"],
                    record.get("description", ""),
                    record.get("status", "authoring"),
                    now,
                    now,
                ),
            )
        return self.get_library_book(book_id)

    def get_library_book(self, book_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_books WHERE book_id = ?", (book_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown library book: {book_id}")
        return dict(row)

    def list_library_books(self, domain_id: str = "") -> list[dict[str, Any]]:
        where = "WHERE domain_id = ?" if domain_id else ""
        values: tuple[Any, ...] = (domain_id,) if domain_id else ()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM library_books {where} ORDER BY updated_at DESC", values
            ).fetchall()
        return [dict(row) for row in rows]

    def create_book_revision(self, record: dict[str, Any]) -> dict[str, Any]:
        revision_id = str(record.get("revision_id") or new_id("revision"))
        with self._connect() as conn:
            latest = conn.execute(
                "SELECT current_revision FROM library_books WHERE book_id = ?",
                (record["book_id"],),
            ).fetchone()
            if latest is None:
                raise KeyError(f"Unknown library book: {record['book_id']}")
            revision = int(latest["current_revision"]) + 1
            conn.execute(
                """
                INSERT INTO library_book_revisions (
                    revision_id, book_id, revision, readiness_report_id,
                    content_sha256, previous_sha256, markdown_path, manifest_path,
                    diff_summary, reason, citations_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    record["book_id"],
                    revision,
                    record["readiness_report_id"],
                    record["content_sha256"],
                    record.get("previous_sha256", ""),
                    record["markdown_path"],
                    record["manifest_path"],
                    record.get("diff_summary", ""),
                    record.get("reason", ""),
                    _json(record.get("citations", [])),
                    iso_now(),
                ),
            )
            conn.execute(
                """
                UPDATE library_books
                SET current_revision = ?, status = ?, updated_at = ?
                WHERE book_id = ?
                """,
                (revision, record.get("book_status", "published"), iso_now(), record["book_id"]),
            )
        return self.get_book_revision(revision_id)

    def find_book_revision_by_hash(
        self, book_id: str, content_sha256: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM library_book_revisions
                WHERE book_id = ? AND content_sha256 = ?
                """,
                (book_id, content_sha256),
            ).fetchone()
        return self._revision_from_row(row) if row is not None else None

    def get_book_revision(self, revision_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_book_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown library revision: {revision_id}")
        return self._revision_from_row(row)

    def list_book_revisions(self, book_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM library_book_revisions WHERE book_id = ?
                ORDER BY revision DESC
                """,
                (book_id,),
            ).fetchall()
        return [self._revision_from_row(row) for row in rows]

    def create_library_suggestion(self, record: dict[str, Any]) -> dict[str, Any]:
        suggestion_id = str(record.get("suggestion_id") or new_id("suggestion"))
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO library_suggestions (
                    suggestion_id, book_id, revision_id, suggestion_type,
                    content, status, mission_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id,
                    record["book_id"],
                    record.get("revision_id"),
                    record.get("suggestion_type", "revision"),
                    record["content"],
                    record.get("status", "queued"),
                    record.get("mission_id"),
                    now,
                    now,
                ),
            )
        return self.get_library_suggestion(suggestion_id)

    def get_library_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_suggestions WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown library suggestion: {suggestion_id}")
        return dict(row)

    def list_library_suggestions(self, book_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM library_suggestions WHERE book_id = ?
                ORDER BY created_at DESC
                """,
                (book_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _load(value.pop("metadata_json"))
        return value

    @staticmethod
    def _usage_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["evidence_ids"] = _load(value.pop("evidence_ids_json"))
        value["artifact_ids"] = _load(value.pop("artifact_ids_json"))
        return value

    @staticmethod
    def _archive_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["manifest"] = _load(value.pop("manifest_json"))
        value["remove_local"] = bool(value["remove_local"])
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

    @staticmethod
    def _domain_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["knowledge_selector"] = _load(value.pop("knowledge_selector_json"))
        value["thresholds"] = _load(value.pop("thresholds_json"))
        return value

    @staticmethod
    def _relation_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["evidence_ids"] = _load(value.pop("evidence_ids_json"))
        value["verified"] = bool(value["verified"])
        return value

    @staticmethod
    def _readiness_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("thresholds", "metrics", "gates", "critical_gaps"):
            value[key] = _load(value.pop(f"{key}_json"))
        value["passed"] = bool(value["passed"])
        return value

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["citations"] = _load(value.pop("citations_json"))
        return value
