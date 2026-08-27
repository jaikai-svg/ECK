from __future__ import annotations

import sqlite3
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class EvolutionOpportunityRepositoryMixin(SQLiteRepositoryMixin):
    """Durable, deduplicated failure evidence for autonomous evolution planning."""

    def create_evolution_opportunity(self, record: dict[str, Any]) -> dict[str, Any]:
        opportunity_id = str(record.get("opportunity_id") or new_id("evolution-opportunity"))
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evolution_opportunities (
                    opportunity_id, signature_sha256, status, title, objective,
                    event_type, worker, failure_type, occurrence_count,
                    evidence_sequences_json, evidence_event_ids_json,
                    target_files_json, test_files_json, heldout_pack_id,
                    candidate_id, readiness_json, error, first_seen_at,
                    last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id,
                    record["signature_sha256"],
                    record["status"],
                    record["title"],
                    record["objective"],
                    record["event_type"],
                    record.get("worker", ""),
                    record.get("failure_type", ""),
                    int(record.get("occurrence_count", 1)),
                    _json(record.get("evidence_sequences", [])),
                    _json(record.get("evidence_event_ids", [])),
                    _json(record.get("target_files", [])),
                    _json(record.get("test_files", [])),
                    record.get("heldout_pack_id"),
                    record.get("candidate_id"),
                    _json(record.get("readiness", {})),
                    record.get("error", ""),
                    record.get("first_seen_at") or now,
                    record.get("last_seen_at") or now,
                    record.get("created_at") or now,
                    now,
                ),
            )
        return self.get_evolution_opportunity(opportunity_id)

    def get_evolution_opportunity(self, opportunity_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_opportunities WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown evolution opportunity: {opportunity_id}")
        return self._evolution_opportunity_from_row(row)

    def get_evolution_opportunity_by_signature(
        self, signature_sha256: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_opportunities WHERE signature_sha256 = ?",
                (signature_sha256,),
            ).fetchone()
        return self._evolution_opportunity_from_row(row) if row is not None else None

    def list_evolution_opportunities(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evolution_opportunities"
        values: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY last_seen_at DESC, opportunity_id LIMIT ?"
        values.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._evolution_opportunity_from_row(row) for row in rows]

    def update_evolution_opportunity(
        self,
        opportunity_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        json_fields = {
            "evidence_sequences",
            "evidence_event_ids",
            "target_files",
            "test_files",
            "readiness",
        }
        allowed = {
            "status",
            "title",
            "objective",
            "occurrence_count",
            "evidence_sequences",
            "evidence_event_ids",
            "target_files",
            "test_files",
            "heldout_pack_id",
            "candidate_id",
            "readiness",
            "error",
            "first_seen_at",
            "last_seen_at",
        }
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported evolution opportunity field: {key}")
            column = f"{key}_json" if key in json_fields else key
            assignments.append(f"{column} = ?")
            values.append(_json(value) if key in json_fields else value)
        values.append(opportunity_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE evolution_opportunities SET {', '.join(assignments)} "
                "WHERE opportunity_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown evolution opportunity: {opportunity_id}")
        return self.get_evolution_opportunity(opportunity_id)

    @staticmethod
    def _evolution_opportunity_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for field, default in (
            ("evidence_sequences", []),
            ("evidence_event_ids", []),
            ("target_files", []),
            ("test_files", []),
            ("readiness", {}),
        ):
            value[field] = _load(value.pop(f"{field}_json"), default)
        return value
