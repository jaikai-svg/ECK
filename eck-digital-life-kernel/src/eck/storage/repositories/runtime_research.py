from __future__ import annotations

import sqlite3
import zlib
from datetime import datetime
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import (
    RuntimeSkillManifest,
    RuntimeSkillRecord,
    RuntimeVersionRecord,
    SupervisorReviewRecord,
)
from eck.research.dedup import simhash_distance
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class RuntimeResearchRepositoryMixin(SQLiteRepositoryMixin):
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
                    SELECT snapshot.*, content.content_sha256
                    FROM research_source_snapshots AS snapshot
                    JOIN research_contents AS content
                      ON content.content_id = snapshot.content_id
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
                    "content_sha256": source["content_sha256"],
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


