from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    columns: tuple[tuple[str, str, int, str | None, int], ...]
    row_count: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    integrity_ok: bool
    foreign_key_violations: int
    user_version: int
    tables: dict[str, TableSnapshot]


@dataclass(frozen=True, slots=True)
class MigrationVerificationReport:
    success: bool
    source_stability_required: bool
    source_unchanged: bool
    upgrade_completed: bool
    data_preserved: bool
    schema_backward_compatible: bool
    rollback_verified: bool
    source_path: str
    pre_upgrade_copy: str
    upgraded_copy: str
    rollback_copy: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class SQLiteMigrationVerifier:
    """Verify a migration against a hot backup without mutating the source database."""

    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path.resolve()

    def verify(
        self,
        upgrade: Callable[[Path], None],
        *,
        output_dir: Path,
        require_source_unchanged: bool = True,
    ) -> MigrationVerificationReport:
        if not self.source_path.is_file():
            raise FileNotFoundError(self.source_path)
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        pre_upgrade = output_dir / "pre-upgrade.sqlite3"
        working = output_dir / "migration-working.sqlite3"
        upgraded = output_dir / "upgraded.sqlite3"
        rollback = output_dir / "rollback.sqlite3"
        for path in (pre_upgrade, working, upgraded, rollback):
            if path.exists():
                raise FileExistsError(path)

        errors: list[str] = []
        warnings: list[str] = []
        self._backup(self.source_path, pre_upgrade)
        source_before = self.snapshot(self.source_path)
        before = self.snapshot(pre_upgrade)
        self._backup(pre_upgrade, working)

        upgrade_completed = False
        data_preserved = False
        schema_compatible = False
        try:
            upgrade(working)
            upgrade_completed = True
            after = self.snapshot(working)
            data_preserved, data_errors = self._data_preserved(
                pre_upgrade,
                working,
                before,
                after,
            )
            schema_compatible, schema_errors = self._schema_compatible(before, after)
            errors.extend(data_errors)
            errors.extend(schema_errors)
            if not after.integrity_ok:
                errors.append("SQLite integrity_check failed after upgrade.")
            if after.foreign_key_violations:
                errors.append("Foreign-key violations were introduced by the upgrade.")
            self._backup(working, upgraded)
        except Exception as exc:
            errors.append(f"Upgrade failed: {type(exc).__name__}: {exc}")

        self._backup(pre_upgrade, rollback)
        rolled_back = self.snapshot(rollback)
        rollback_verified = rolled_back == before
        if not rollback_verified:
            errors.append("Rollback copy does not match the pre-upgrade snapshot.")

        source_after = self.snapshot(self.source_path)
        source_unchanged = source_after == source_before
        if not source_unchanged and require_source_unchanged:
            errors.append("Source database changed during migration verification.")
        elif not source_unchanged:
            warnings.append(
                "Live source advanced during verification; migration checks used the frozen "
                "pre-upgrade backup."
            )

        success = bool(
            upgrade_completed
            and data_preserved
            and schema_compatible
            and rollback_verified
            and (source_unchanged or not require_source_unchanged)
            and not errors
        )
        return MigrationVerificationReport(
            success=success,
            source_stability_required=require_source_unchanged,
            source_unchanged=source_unchanged,
            upgrade_completed=upgrade_completed,
            data_preserved=data_preserved,
            schema_backward_compatible=schema_compatible,
            rollback_verified=rollback_verified,
            source_path=str(self.source_path),
            pre_upgrade_copy=str(pre_upgrade),
            upgraded_copy=str(upgraded),
            rollback_copy=str(rollback),
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    @classmethod
    def snapshot(cls, path: Path) -> DatabaseSnapshot:
        with sqlite3.connect(path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            tables = {name: cls._table_snapshot(connection, name) for name in names}
        return DatabaseSnapshot(
            integrity_ok=integrity == [("ok",)],
            foreign_key_violations=len(foreign_keys),
            user_version=user_version,
            tables=tables,
        )

    @classmethod
    def _table_snapshot(
        cls,
        connection: sqlite3.Connection,
        table_name: str,
    ) -> TableSnapshot:
        quoted_table = cls._quote_identifier(table_name)
        column_rows = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        columns = tuple(
            (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
            for row in column_rows
        )
        column_names = tuple(item[0] for item in columns)
        row_count, content_sha256 = cls._content_digest(
            connection,
            table_name,
            column_names,
        )
        return TableSnapshot(
            columns=columns,
            row_count=row_count,
            content_sha256=content_sha256,
        )

    @classmethod
    def _data_preserved(
        cls,
        before_path: Path,
        after_path: Path,
        before: DatabaseSnapshot,
        after: DatabaseSnapshot,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        with (
            sqlite3.connect(before_path) as before_connection,
            sqlite3.connect(after_path) as after_connection,
        ):
            for name, original in before.tables.items():
                migrated = after.tables.get(name)
                if migrated is None:
                    errors.append(f"Existing table was removed: {name}")
                    continue
                column_names = tuple(column[0] for column in original.columns)
                original_count, original_digest = cls._content_digest(
                    before_connection,
                    name,
                    column_names,
                )
                migrated_count, migrated_digest = cls._content_digest(
                    after_connection,
                    name,
                    column_names,
                )
                if original_count != migrated_count:
                    errors.append(f"Row count changed in existing table: {name}")
                if original_digest != migrated_digest:
                    errors.append(f"Existing row content changed: {name}")
        return not errors, errors

    @classmethod
    def _content_digest(
        cls,
        connection: sqlite3.Connection,
        table_name: str,
        column_names: tuple[str, ...],
    ) -> tuple[int, str]:
        quoted_table = cls._quote_identifier(table_name)
        quoted_columns = ", ".join(cls._quote_identifier(name) for name in column_names)
        order_by = f" ORDER BY {quoted_columns}" if quoted_columns else ""
        rows = connection.execute(
            f"SELECT {quoted_columns} FROM {quoted_table}{order_by}"
        ).fetchall()
        digest = hashlib.sha256()
        for row in rows:
            payload = [cls._normalize_value(value) for value in row]
            digest.update(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            digest.update(b"\n")
        return len(rows), digest.hexdigest()

    @staticmethod
    def _schema_compatible(
        before: DatabaseSnapshot,
        after: DatabaseSnapshot,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for name, original in before.tables.items():
            migrated = after.tables.get(name)
            if migrated is None:
                continue
            migrated_columns = {column[0]: column for column in migrated.columns}
            for column in original.columns:
                if migrated_columns.get(column[0]) != column:
                    errors.append(f"Existing column changed or was removed: {name}.{column[0]}")
        return not errors, errors

    @staticmethod
    def _backup(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(source) as source_connection,
            sqlite3.connect(target) as target_connection,
        ):
            source_connection.backup(target_connection)

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"type": "bytes", "hex": value.hex()}
        if isinstance(value, float):
            return {"type": "float", "value": value.hex()}
        return value


def copy_verification_artifacts(report: MigrationVerificationReport, target: Path) -> None:
    """Copy a successful verification bundle for external audit or archival."""

    if not report.success:
        raise ValueError("Only successful migration verification artifacts can be archived.")
    target.mkdir(parents=True, exist_ok=False)
    for source in (report.pre_upgrade_copy, report.upgraded_copy, report.rollback_copy):
        shutil.copy2(source, target / Path(source).name)
    (target / "report.json").write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
