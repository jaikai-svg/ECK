from __future__ import annotations

import sqlite3
from pathlib import Path

from eck.storage.migration import SQLiteMigrationVerifier
from eck.storage.sqlite import SQLiteStore


def _legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE records (record_id TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO records (record_id, value) VALUES (?, ?)",
            (("record-1", "alpha"), ("record-2", "beta")),
        )


def test_migration_verifier_preserves_old_data_and_proves_rollback(tmp_path: Path) -> None:
    source = tmp_path / "legacy.sqlite3"
    _legacy_database(source)

    def upgrade(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute("ALTER TABLE records ADD COLUMN note TEXT")
            connection.execute(
                "CREATE TABLE migration_metadata (version INTEGER NOT NULL)"
            )
            connection.execute("INSERT INTO migration_metadata VALUES (2)")

    report = SQLiteMigrationVerifier(source).verify(
        upgrade,
        output_dir=tmp_path / "verification",
    )

    assert report.success
    assert report.source_unchanged
    assert report.data_preserved
    assert report.schema_backward_compatible
    assert report.rollback_verified


def test_migration_verifier_rejects_destructive_upgrade_without_touching_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.sqlite3"
    _legacy_database(source)
    before = SQLiteMigrationVerifier.snapshot(source)

    def destructive_upgrade(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM records WHERE record_id = 'record-1'")

    report = SQLiteMigrationVerifier(source).verify(
        destructive_upgrade,
        output_dir=tmp_path / "verification",
    )

    assert not report.success
    assert not report.data_preserved
    assert report.rollback_verified
    assert report.source_unchanged
    assert SQLiteMigrationVerifier.snapshot(source) == before


def test_workspace_phase2_schema_upgrades_old_eck_copy_and_rolls_back(
    tmp_path: Path,
) -> None:
    source = tmp_path / "eck-before-phase2.sqlite3"
    SQLiteStore(source).initialize()
    phase2_tables = (
        "library_suggestions",
        "library_book_revisions",
        "library_books",
        "library_readiness_reports",
        "knowledge_relations",
        "library_domain_cards",
        "library_domains",
        "artifact_cache_entries",
        "archive_records",
        "task_skill_usages",
        "artifact_index",
    )
    with sqlite3.connect(source) as connection:
        for table in phase2_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            """
            INSERT INTO kernel_state (
                identity, phase, boot_count, clean_shutdown, updated_at
            ) VALUES ('legacy-eck', 'sleeping', 7, 1, '2026-08-01T00:00:00+00:00')
            """
        )

    report = SQLiteMigrationVerifier(source).verify(
        lambda path: SQLiteStore(path).initialize(),
        output_dir=tmp_path / "phase2-verification",
    )

    assert report.success
    assert report.data_preserved
    assert report.rollback_verified
    upgraded = SQLiteMigrationVerifier.snapshot(Path(report.upgraded_copy))
    assert set(phase2_tables) <= set(upgraded.tables)
    with sqlite3.connect(report.upgraded_copy) as connection:
        row = connection.execute(
            "SELECT boot_count FROM kernel_state WHERE identity = 'legacy-eck'"
        ).fetchone()
    assert row == (7,)
