from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def recover_failed_activation(
    repo_root: Path,
    database_path: Path,
    *,
    maximum_age_seconds: int = 600,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    database_path = database_path.resolve()
    if not (repo_root / ".git").exists() or not database_path.is_file():
        return {"recovered": False, "reason": "repository_or_database_missing"}
    with sqlite3.connect(database_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT * FROM evolution_transactions
            WHERE status IN ('restart_pending', 'activation_applying')
            ORDER BY activation_requested_at DESC, updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return {"recovered": False, "reason": "no_pending_activation"}
        transaction = dict(row)
        requested_at = _parse_datetime(transaction.get("activation_requested_at"))
        age = (datetime.now(UTC) - requested_at).total_seconds()
        if age < 0 or age > maximum_age_seconds:
            return {"recovered": False, "reason": "pending_activation_outside_recovery_window"}
        expected = str(transaction.get("expected_commit_sha") or "")
        previous = str(transaction.get("previous_commit_sha") or "")
        status = str(transaction["status"])
        if not previous or (status == "restart_pending" and not expected):
            return {"recovered": False, "reason": "rollback_authority_missing"}
        observed = _git(repo_root, "rev-parse", "HEAD").strip()
        if status == "restart_pending":
            if observed != expected:
                return {"recovered": False, "reason": "head_does_not_match_pending_commit"}
            if _git(repo_root, "status", "--porcelain").strip():
                return {"recovered": False, "reason": "working_tree_not_clean"}
        else:
            candidate_tree = str(transaction.get("candidate_tree_sha") or "")
            if observed == previous:
                working_status = _git(repo_root, "status", "--porcelain").splitlines()
                staged_tree = _git(repo_root, "write-tree").strip()
                previous_tree = _git(repo_root, "rev-parse", f"{previous}^{{tree}}").strip()
                if staged_tree == previous_tree and not working_status:
                    connection.execute(
                        """
                        UPDATE evolution_transactions
                        SET status = 'approved', error = ?, updated_at = ?
                        WHERE transaction_id = ? AND status = 'activation_applying'
                        """,
                        (
                            "Activation stopped before the candidate patch was applied.",
                            datetime.now(UTC).isoformat(),
                            transaction["transaction_id"],
                        ),
                    )
                    connection.commit()
                    return {"recovered": False, "reason": "activation_not_applied"}
                unstaged = _git(repo_root, "diff", "--name-only", "--", ".").strip()
                has_untracked = any(line.startswith("??") for line in working_status)
                if staged_tree != candidate_tree or unstaged or has_untracked:
                    return {"recovered": False, "reason": "activation_state_not_exact"}
                expected = observed
            else:
                parent = _git(repo_root, "rev-parse", f"{observed}^").strip()
                observed_tree = _git(repo_root, "rev-parse", f"{observed}^{{tree}}").strip()
                if parent != previous or observed_tree != candidate_tree:
                    return {"recovered": False, "reason": "activation_commit_not_exact"}
                if _git(repo_root, "status", "--porcelain").strip():
                    return {"recovered": False, "reason": "working_tree_not_clean"}
                expected = observed
        _git(repo_root, "reset", "--hard", previous)
        restored = _git(repo_root, "rev-parse", "HEAD").strip()
        if restored != previous:
            raise RuntimeError("Git rollback did not restore the expected previous commit.")
        now = datetime.now(UTC).isoformat()
        restart_nonce = f"external-recovery_{uuid.uuid4().hex}"
        details = json.dumps(
            {
                "mode": "catastrophic_startup_recovery",
                "failed_commit_sha": expected,
                "restored_commit_sha": previous,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE evolution_transactions
            SET status = 'rollback_restart_pending',
                expected_commit_sha = ?, rollback_commit_sha = ?,
                restart_nonce = ?, error = ?, updated_at = ?
            WHERE transaction_id = ?
              AND status IN ('restart_pending', 'activation_applying')
            """,
            (
                previous,
                previous,
                restart_nonce,
                "External supervisor restored the previous commit after startup failure.",
                now,
                transaction["transaction_id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO evolution_boot_receipts (
                receipt_id, transaction_id, expected_commit_sha,
                observed_commit_sha, boot_count, status, details_json, created_at
            ) VALUES (?, ?, ?, ?, 0, 'startup_failed_external', ?, ?)
            """,
            (
                f"evolution-boot_{uuid.uuid4().hex}",
                transaction["transaction_id"],
                expected,
                observed,
                details,
                now,
            ),
        )
        connection.commit()
    return {
        "recovered": True,
        "transaction_id": transaction["transaction_id"],
        "failed_commit_sha": expected,
        "restored_commit_sha": previous,
        "restart_nonce": restart_nonce,
    }


def _parse_datetime(value: object) -> datetime:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
        raise RuntimeError(detail)
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--maximum-age-seconds", type=int, default=600)
    arguments = parser.parse_args()
    result = recover_failed_activation(
        arguments.repo_root,
        arguments.database,
        maximum_age_seconds=max(60, arguments.maximum_age_seconds),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 20 if result["recovered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
