from __future__ import annotations

import sqlite3
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class EvolutionTransactionRepositoryMixin(SQLiteRepositoryMixin):
    """Durable evidence for reviewed structural evolution."""

    def upsert_evolution_transaction(self, record: dict[str, Any]) -> dict[str, Any]:
        now = iso_now()
        candidate_id = str(record["candidate_id"])
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT transaction_id FROM evolution_transactions WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            transaction_id = (
                str(existing["transaction_id"])
                if existing is not None
                else str(record.get("transaction_id") or new_id("evolution-tx"))
            )
            conn.execute(
                """
                INSERT INTO evolution_transactions (
                    transaction_id, candidate_id, status, base_commit,
                    base_tree_sha256, candidate_tree_sha, patch_sha256,
                    manifest_sha256, protected_paths_json, fixed_gates_json,
                    approval_json, expected_commit_sha, previous_commit_sha,
                    rollback_commit_sha, restart_nonce, error, created_at,
                    updated_at, approved_at, activation_requested_at,
                    restart_verified_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', NULL, NULL,
                          NULL, NULL, '', ?, ?, NULL, NULL, NULL, NULL)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    status = excluded.status,
                    base_commit = excluded.base_commit,
                    base_tree_sha256 = excluded.base_tree_sha256,
                    candidate_tree_sha = excluded.candidate_tree_sha,
                    patch_sha256 = excluded.patch_sha256,
                    manifest_sha256 = excluded.manifest_sha256,
                    protected_paths_json = excluded.protected_paths_json,
                    fixed_gates_json = excluded.fixed_gates_json,
                    updated_at = excluded.updated_at
                """,
                (
                    transaction_id,
                    candidate_id,
                    record["status"],
                    record.get("base_commit", ""),
                    record.get("base_tree_sha256", ""),
                    record.get("candidate_tree_sha", ""),
                    record.get("patch_sha256", ""),
                    record.get("manifest_sha256", ""),
                    _json(record.get("protected_paths", [])),
                    _json(record.get("fixed_gates", {})),
                    record.get("created_at") or now,
                    now,
                ),
            )
        return self.get_evolution_transaction(transaction_id)

    def get_evolution_transaction(self, transaction_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown evolution transaction: {transaction_id}")
        return self._evolution_transaction_from_row(row)

    def get_evolution_transaction_for_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_transactions WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown core candidate transaction: {candidate_id}")
        return self._evolution_transaction_from_row(row)

    def list_evolution_transactions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_transactions "
                "ORDER BY created_at DESC, transaction_id LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._evolution_transaction_from_row(row) for row in rows]

    def update_evolution_transaction(
        self,
        transaction_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        json_fields = {"approval", "fixed_gates", "protected_paths"}
        allowed = {
            "status",
            "candidate_tree_sha",
            "manifest_sha256",
            "approval",
            "fixed_gates",
            "protected_paths",
            "expected_commit_sha",
            "previous_commit_sha",
            "rollback_commit_sha",
            "restart_nonce",
            "error",
            "approved_at",
            "activation_requested_at",
            "restart_verified_at",
            "completed_at",
        }
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported evolution transaction field: {key}")
            column = f"{key}_json" if key in json_fields else key
            assignments.append(f"{column} = ?")
            values.append(_json(value) if key in json_fields else value)
        values.append(transaction_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE evolution_transactions SET {', '.join(assignments)} "
                "WHERE transaction_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown evolution transaction: {transaction_id}")
        return self.get_evolution_transaction(transaction_id)

    def create_evolution_evaluation(self, record: dict[str, Any]) -> dict[str, Any]:
        evaluation_id = str(record.get("evaluation_id") or new_id("evolution-eval"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evolution_evaluations (
                    evaluation_id, transaction_id, pack_id, pack_sha256,
                    baseline_json, candidate_json, result_json, verdict,
                    improvement_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    record["transaction_id"],
                    record["pack_id"],
                    record["pack_sha256"],
                    _json(record.get("baseline", {})),
                    _json(record.get("candidate", {})),
                    _json(record.get("result", {})),
                    record["verdict"],
                    float(record.get("improvement_score", 0.0)),
                    record.get("created_at") or iso_now(),
                ),
            )
        return self.get_evolution_evaluation(evaluation_id)

    def get_evolution_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown evolution evaluation: {evaluation_id}")
        return self._evolution_evaluation_from_row(row)

    def list_evolution_evaluations(self, transaction_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_evaluations WHERE transaction_id = ? "
                "ORDER BY created_at DESC, evaluation_id",
                (transaction_id,),
            ).fetchall()
        return [self._evolution_evaluation_from_row(row) for row in rows]

    def create_evolution_boot_receipt(self, record: dict[str, Any]) -> dict[str, Any]:
        receipt_id = str(record.get("receipt_id") or new_id("evolution-boot"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evolution_boot_receipts (
                    receipt_id, transaction_id, expected_commit_sha,
                    observed_commit_sha, boot_count, status, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    record["transaction_id"],
                    record["expected_commit_sha"],
                    record.get("observed_commit_sha", ""),
                    int(record.get("boot_count", 0)),
                    record["status"],
                    _json(record.get("details", {})),
                    record.get("created_at") or iso_now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM evolution_boot_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        assert row is not None
        return self._evolution_boot_receipt_from_row(row)

    def list_evolution_boot_receipts(self, transaction_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_boot_receipts WHERE transaction_id = ? "
                "ORDER BY created_at DESC, receipt_id",
                (transaction_id,),
            ).fetchall()
        return [self._evolution_boot_receipt_from_row(row) for row in rows]

    @staticmethod
    def _evolution_transaction_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["protected_paths"] = _load(value.pop("protected_paths_json"), [])
        value["fixed_gates"] = _load(value.pop("fixed_gates_json"), {})
        value["approval"] = _load(value.pop("approval_json"), {})
        return value

    @staticmethod
    def _evolution_evaluation_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["baseline"] = _load(value.pop("baseline_json"), {})
        value["candidate"] = _load(value.pop("candidate_json"), {})
        value["result"] = _load(value.pop("result_json"), {})
        return value

    @staticmethod
    def _evolution_boot_receipt_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["details"] = _load(value.pop("details_json"), {})
        return value
