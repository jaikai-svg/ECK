from __future__ import annotations

import sqlite3
import threading
from contextlib import AbstractContextManager
from pathlib import Path


class SQLiteRepositoryMixin:
    path: Path
    _chain_lock: threading.Lock
    _verified_sequence: int
    _verified_hash: str
    _chain_valid: bool
    _chain_failed_sequence: int | None

    def _connect(self) -> AbstractContextManager[sqlite3.Connection]:
        raise NotImplementedError

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



