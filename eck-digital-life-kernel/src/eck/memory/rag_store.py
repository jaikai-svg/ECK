from __future__ import annotations

import importlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class PortableVectorStore:
    """Portable sqlite-vec store kept separate from the core compatibility database."""

    def __init__(self, path: Path, *, dimension: int) -> None:
        self.path = path.resolve()
        self.dimension = dimension

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS rag_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_documents (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rag_documents_source
                    ON rag_documents(source_type, document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_embeddings USING vec0(
                    embedding float[{self.dimension}]
                );
                """
            )
            existing = connection.execute(
                "SELECT value FROM rag_meta WHERE key = 'dimension'"
            ).fetchone()
            if existing is not None and int(existing["value"]) != self.dimension:
                raise RuntimeError("The portable RAG database has a different vector dimension.")
            connection.execute(
                "INSERT OR REPLACE INTO rag_meta(key, value) VALUES ('dimension', ?)",
                (str(self.dimension),),
            )

    def hashes(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id, content_sha256 FROM rag_documents"
            ).fetchall()
        return {str(row["document_id"]): str(row["content_sha256"]) for row in rows}

    def upsert(self, document: dict[str, Any], embedding: list[float]) -> bool:
        if len(embedding) != self.dimension:
            raise ValueError("Embedding dimension does not match the sqlite-vec schema.")
        serialized = self._serialize(embedding)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT rowid, content_sha256 FROM rag_documents WHERE document_id = ?",
                (document["document_id"],),
            ).fetchone()
            if existing is not None and existing["content_sha256"] == document["content_sha256"]:
                return False
            values = (
                document["source_type"],
                document["title"],
                document["content"],
                document["source_uri"],
                document["content_sha256"],
                json.dumps(document["metadata"], ensure_ascii=False, sort_keys=True),
                document["updated_at"],
            )
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO rag_documents(
                        document_id, source_type, title, content, source_uri,
                        content_sha256, metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (document["document_id"], *values),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return a row id for the RAG document.")
                rowid = cursor.lastrowid
            else:
                rowid = int(existing["rowid"])
                connection.execute("DELETE FROM rag_embeddings WHERE rowid = ?", (rowid,))
                connection.execute(
                    """
                    UPDATE rag_documents SET source_type = ?, title = ?, content = ?,
                        source_uri = ?, content_sha256 = ?, metadata_json = ?, updated_at = ?
                    WHERE rowid = ?
                    """,
                    (*values, rowid),
                )
            connection.execute(
                "INSERT INTO rag_embeddings(rowid, embedding) VALUES (?, ?)",
                (rowid, serialized),
            )
        return True

    def search(self, embedding: list[float], *, limit: int) -> list[dict[str, Any]]:
        if len(embedding) != self.dimension:
            raise ValueError("Query embedding dimension does not match the sqlite-vec schema.")
        if limit <= 0 or self.count() == 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT document.*, matches.distance
                FROM rag_embeddings AS matches
                JOIN rag_documents AS document ON document.rowid = matches.rowid
                WHERE matches.embedding MATCH ? AND k = ?
                ORDER BY matches.distance
                """,
                (self._serialize(embedding), min(limit, self.count())),
            ).fetchall()
        return [
            {
                "document_id": row["document_id"],
                "source_type": row["source_type"],
                "title": row["title"],
                "content": row["content"],
                "source_uri": row["source_uri"],
                "content_sha256": row["content_sha256"],
                "metadata": json.loads(row["metadata_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM rag_documents").fetchone()
        return int(row["count"])

    def integrity(self) -> dict[str, Any]:
        with self._connect() as connection:
            document_count = int(
                connection.execute("SELECT COUNT(*) AS count FROM rag_documents").fetchone()[
                    "count"
                ]
            )
            vector_count = int(
                connection.execute("SELECT COUNT(*) AS count FROM rag_embeddings").fetchone()[
                    "count"
                ]
            )
            result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        return {
            "valid": result == "ok" and document_count == vector_count,
            "integrity_check": result,
            "documents": document_count,
            "vectors": vector_count,
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.enable_load_extension(True)
            sqlite_vec = importlib.import_module("sqlite_vec")
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _serialize(embedding: list[float]) -> bytes:
        sqlite_vec = importlib.import_module("sqlite_vec")
        return bytes(sqlite_vec.serialize_float32(embedding))
