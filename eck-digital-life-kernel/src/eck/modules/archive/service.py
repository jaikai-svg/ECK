from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any

from eck.config import Settings
from eck.storage.sqlite import SQLiteStore


class ArchiveIntegrityError(RuntimeError):
    pass


class ArchiveOfflineError(RuntimeError):
    pass


class ArchiveService:
    """Verified filesystem archive provider with a bounded local LRU cache."""

    def __init__(self, settings: Settings, store: SQLiteStore) -> None:
        self.settings = settings
        self.store = store
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        root = self.settings.archive_root
        configured = root is not None
        online = bool(root and root.exists() and root.is_dir())
        entries = self.store.list_cache_entries()
        return {
            "provider": "filesystem",
            "configured": configured,
            "online": online,
            "state": "online" if online else ("offline" if configured else "unconfigured"),
            "root": str(root) if root else "",
            "cache": {
                "path": str(self.settings.archive_cache_dir or ""),
                "size_bytes": sum(int(item["size_bytes"]) for item in entries),
                "max_bytes": self._cache_limit,
                "entries": len(entries),
                "in_use": sum(int(item["in_use_count"]) for item in entries),
            },
        }

    def archive(self, artifact_id: str, *, remove_local: bool | None = None) -> dict[str, Any]:
        with self._lock:
            root = self._online_root()
            artifact = self.store.get_artifact(artifact_id)
            source = Path(str(artifact["local_path"]))
            if not source.exists():
                raise FileNotFoundError(f"Local artifact is unavailable: {source}")
            remove = (
                self.settings.archive_remove_local_after_verify
                if remove_local is None
                else remove_local
            )
            manifest = self.build_manifest(source)
            final = root / artifact_id
            temporary = root / f".{artifact_id}.partial"
            backup = root / f".{artifact_id}.backup"
            record = self.store.create_archive_record(
                {
                    "artifact_id": artifact_id,
                    "source_path": str(source),
                    "archive_path": str(final),
                    "manifest": manifest,
                    "status": "copying",
                    "content_sha256": manifest["content_sha256"],
                    "file_count": manifest["file_count"],
                    "size_bytes": manifest["size_bytes"],
                    "remove_local": remove,
                }
            )
            try:
                if temporary.exists():
                    shutil.rmtree(temporary)
                temporary.mkdir(parents=True)
                payload = temporary / "payload"
                if source.is_file():
                    payload.mkdir()
                    shutil.copy2(source, payload / source.name)
                else:
                    self._copy(source, payload)
                self._write_json(temporary / "manifest.json", manifest)
                copied = self.build_manifest(payload)
                if not self._manifests_match(manifest, copied):
                    raise ArchiveIntegrityError("Archive copy failed SHA-256 verification.")
                self._remove(backup)
                if final.exists():
                    final.replace(backup)
                temporary.replace(final)
                self._remove(backup)
                if remove:
                    self._remove(source)
                    storage_state = "nas"
                else:
                    storage_state = "local+nas"
                self.store.set_artifact_storage(
                    artifact_id,
                    storage_state=storage_state,
                    integrity_status="verified",
                )
                return self.store.update_archive_record(
                    str(record["archive_id"]), status="verified", error=""
                )
            except Exception as exc:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
                if backup.exists() and not final.exists():
                    backup.replace(final)
                self.store.update_archive_record(
                    str(record["archive_id"]),
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise

    def acquire(self, artifact_id: str) -> Path:
        with self._lock:
            artifact = self.store.get_artifact(artifact_id)
            local = Path(str(artifact["local_path"]))
            if local.exists():
                return local
            archive = self.store.latest_archive_for_artifact(artifact_id)
            if not archive or archive["status"] != "verified":
                raise FileNotFoundError(f"No verified archive exists for {artifact_id}")
            root = self._online_root()
            archive_dir = Path(str(archive["archive_path"]))
            try:
                archive_dir.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise ArchiveIntegrityError("Archive path escaped configured root.") from exc
            manifest = self._read_json(archive_dir / "manifest.json")
            payload = archive_dir / "payload"
            if not self._manifests_match(manifest, self.build_manifest(payload)):
                raise ArchiveIntegrityError("Archived artifact is corrupt.")
            cache_root = self.settings.archive_cache_dir
            assert cache_root is not None
            target = cache_root / artifact_id
            existing = self.store.get_cache_entry(artifact_id)
            if target.exists() and existing:
                if self._manifests_match(manifest, self.build_manifest(target)):
                    self.store.change_cache_use(artifact_id, 1)
                    return self._restored_path(target, manifest)
                self._remove(target)
                self.store.delete_cache_entry(artifact_id)
            temporary = cache_root / f".{artifact_id}.partial"
            self._remove(temporary)
            self._copy(payload, temporary)
            if not self._manifests_match(manifest, self.build_manifest(temporary)):
                self._remove(temporary)
                raise ArchiveIntegrityError("Restored cache failed SHA-256 verification.")
            temporary.replace(target)
            self.store.upsert_cache_entry(
                {
                    "artifact_id": artifact_id,
                    "cache_path": str(target),
                    "content_sha256": manifest["content_sha256"],
                    "size_bytes": manifest["size_bytes"],
                    "in_use_count": 1,
                }
            )
            self.store.update_archive_record(
                str(archive["archive_id"]), status="verified", restored_at=self._now()
            )
            self._evict(exclude={artifact_id})
            return self._restored_path(target, manifest)

    def release(self, artifact_id: str) -> None:
        with self._lock:
            if self.store.get_cache_entry(artifact_id):
                self.store.change_cache_use(artifact_id, -1)
                self._evict()

    @property
    def _cache_limit(self) -> int:
        return int(self.settings.archive_cache_max_gb * 1024**3)

    def _online_root(self) -> Path:
        root = self.settings.archive_root
        if root is None:
            raise ArchiveOfflineError("Archive provider is not configured.")
        if not root.exists() or not root.is_dir():
            raise ArchiveOfflineError("Archive provider is offline; files are not marked lost.")
        return root

    def _evict(self, *, exclude: set[str] | None = None) -> None:
        excluded = exclude or set()
        entries = self.store.list_cache_entries()
        total = sum(int(item["size_bytes"]) for item in entries)
        for item in entries:
            artifact_id = str(item["artifact_id"])
            if total <= self._cache_limit:
                break
            if artifact_id in excluded or int(item["in_use_count"]) > 0:
                continue
            self._remove(Path(str(item["cache_path"])))
            self.store.delete_cache_entry(artifact_id)
            total -= int(item["size_bytes"])

    @staticmethod
    def build_manifest(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        root = path if path.is_dir() else path.parent
        files = [path] if path.is_file() else sorted(
            item for item in path.rglob("*") if item.is_file()
        )
        entries = []
        digest = hashlib.sha256()
        total_size = 0
        for item in files:
            relative = item.name if path.is_file() else item.relative_to(root).as_posix()
            file_hash = ArchiveService._file_hash(item)
            size = item.stat().st_size
            total_size += size
            entries.append({"path": relative, "size_bytes": size, "sha256": file_hash})
            digest.update(relative.encode("utf-8"))
            digest.update(bytes.fromhex(file_hash))
        return {
            "schema_version": "eck-archive-manifest.v1",
            "kind": "file" if path.is_file() else "directory",
            "content_sha256": digest.hexdigest(),
            "file_count": len(entries),
            "size_bytes": total_size,
            "files": entries,
        }

    @staticmethod
    def _manifests_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        return all(
            expected.get(key) == actual.get(key)
            for key in ("content_sha256", "file_count", "size_bytes", "files")
        )

    @staticmethod
    def _restored_path(cache_dir: Path, manifest: dict[str, Any]) -> Path:
        if manifest.get("kind") == "file":
            files = manifest.get("files", [])
            if len(files) != 1:
                raise ArchiveIntegrityError("File archive contains an invalid file list.")
            return cache_dir / str(files[0]["path"])
        return cache_dir

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _copy(source: Path, target: Path) -> None:
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ArchiveIntegrityError("Archive manifest is invalid.")
        return value

    @staticmethod
    def _now() -> str:
        from eck.core.time import iso_now

        return iso_now()
