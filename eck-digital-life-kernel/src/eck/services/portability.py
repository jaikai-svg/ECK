from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from eck import __version__
from eck.capabilities.registry import CapabilityRegistry
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.events.bus import EventBus
from eck.services.versioning import VersionService
from eck.storage.sqlite import SQLiteStore


class CognitiveBundleService:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        registry: CapabilityRegistry,
        versions: VersionService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.registry = registry
        self.versions = versions

    async def export(self, *, include_artifacts: bool = False) -> dict[str, Any]:
        bundle_id = new_id("cognitive-bundle")
        stage = self.settings.export_dir / bundle_id
        archive = self.settings.export_dir / f"{bundle_id}.zip"
        stage.mkdir(parents=True, exist_ok=False)
        try:
            self._backup_database(stage / "data" / "eck.db")
            self._backup_rag_database(stage / "memory" / "eck-rag.sqlite3")
            self._copy_generated_skills(stage / "runtime_skills")
            self._copy_cognitive_identity(stage / "identity")
            self._copy_evolution_metadata(stage / "evolution")
            self._copy_autonomous_projects(stage / "projects")
            self._copy_project_metadata(stage / "project")
            if include_artifacts:
                self._copy_artifacts(stage / "artifacts")
            manifest = self._manifest(stage, include_artifacts=include_artifacts)
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            shutil.make_archive(str(archive.with_suffix("")), "zip", stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        verification = self.verify(archive.name)
        await self.events.publish(
            "CognitiveBundleExported",
            bundle_id,
            {
                "archive": archive.name,
                "include_artifacts": include_artifacts,
                "verified": verification["valid"],
            },
        )
        return {
            "bundle_id": bundle_id,
            "archive": archive.name,
            "path": str(archive),
            "download_url": f"/v1/portability/bundles/{archive.name}",
            "verification": verification,
        }

    def verify(self, archive_name: str) -> dict[str, Any]:
        archive = self.bundle_path(archive_name)
        failures: list[str] = []
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            if "manifest.json" not in names:
                raise ValueError("Cognitive bundle has no manifest.json.")
            for name in names:
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Cognitive bundle contains an unsafe path.")
            manifest = json.loads(bundle.read("manifest.json"))
            expected = manifest.get("files", {})
            if not isinstance(expected, dict):
                raise ValueError("Cognitive bundle file inventory is invalid.")
            for name, expected_hash in expected.items():
                if name not in names:
                    failures.append(f"missing:{name}")
                    continue
                digest = hashlib.sha256(bundle.read(name)).hexdigest()
                if digest != expected_hash:
                    failures.append(f"sha256:{name}")
        return {
            "valid": not failures,
            "archive": archive.name,
            "format": manifest.get("format"),
            "files_checked": len(expected),
            "failures": failures,
        }

    def bundle_path(self, archive_name: str) -> Path:
        safe_name = Path(archive_name).name
        if safe_name != archive_name or not safe_name.endswith(".zip"):
            raise ValueError("Invalid cognitive bundle name.")
        archive = (self.settings.export_dir / safe_name).resolve()
        archive.relative_to(self.settings.export_dir.resolve())
        if not archive.is_file():
            raise FileNotFoundError(safe_name)
        return archive

    def _backup_database(self, target: Path) -> None:
        assert self.settings.database_path is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(self.settings.database_path) as source,
            sqlite3.connect(target) as destination,
        ):
            source.backup(destination)

    def _backup_rag_database(self, target: Path) -> None:
        assert self.settings.rag_database_path is not None
        if not self.settings.rag_database_path.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(self.settings.rag_database_path) as source,
            sqlite3.connect(target) as destination,
        ):
            source.backup(destination)

    def _copy_generated_skills(self, target: Path) -> None:
        source = self.settings.workspace_dir / "runtime_skills"
        if source.is_dir():
            shutil.copytree(source, target)

    def _copy_cognitive_identity(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        if self.settings.identity_dir.is_dir():
            shutil.copytree(self.settings.identity_dir, target / "soul")
        for source in (
            self.settings.self_model_path,
            self.settings.research_skill_bridge_state_path,
        ):
            if source.is_file():
                shutil.copy2(source, target / source.name)

    def _copy_evolution_metadata(self, target: Path) -> None:
        source = self.settings.evolution_dir / "core_candidates"
        if source.is_dir():
            shutil.copytree(source, target / "core_candidates")

    def _copy_autonomous_projects(self, target: Path) -> None:
        if self.settings.project_lab_dir.is_dir():
            shutil.copytree(
                self.settings.project_lab_dir,
                target,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
            )
        if self.settings.project_lab_state_path.is_file():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.settings.project_lab_state_path, target / "cycle-state.json")

    def _copy_project_metadata(self, target: Path) -> None:
        project_root = Path(__file__).resolve().parents[3]
        target.mkdir(parents=True, exist_ok=True)
        for relative in (
            Path("pyproject.toml"),
            Path("uv.lock"),
            Path("config/eck.yaml"),
            Path("config/image-models.json"),
            Path("config/community-sources.json"),
            Path("config/cogvideo-requirements.txt"),
        ):
            source = project_root / relative
            if not source.is_file():
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def _copy_artifacts(self, target: Path) -> None:
        sources = (
            (self.settings.image_output_dir, target / "images"),
            (self.settings.video_output_dir, target / "videos"),
        )
        for source, destination in sources:
            if source.is_dir():
                shutil.copytree(source, destination)

    def _manifest(self, stage: Path, *, include_artifacts: bool) -> dict[str, Any]:
        chain_valid, failed_sequence = self.store.verify_event_chain()
        files = {
            path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        }
        return {
            "format": "eck-cognitive-bundle.v2",
            "created_at": utc_now().isoformat(),
            "eck_version": __version__,
            "runtime_version": self.versions.status().model_dump(mode="json"),
            "identity": self.settings.identity,
            "brain": {
                "provider": self.settings.brain_provider,
                "model": self.settings.ollama_model,
            },
            "memory": {
                "experiences": self.store.count_experiences(),
                "verified_experiences": self.store.count_experiences(admitted=True),
                "knowledge": len(self.store.list_knowledge(limit=10000)),
                "reflections": len(self.store.list_reflections(limit=10000)),
                "skills": len(self.store.list_skills(limit=10000)),
                "runtime_skills": len(self.store.list_runtime_skills(limit=10000)),
                "learning_themes": len(self.store.list_learning_themes(limit=10000)),
                "skill_graph_rebuildable": True,
                "portable_rag_database": bool(
                    self.settings.rag_database_path
                    and self.settings.rag_database_path.is_file()
                ),
                "soul_and_lineage": True,
                "repository_self_model": self.settings.self_model_path.is_file(),
            },
            "event_chain": {
                "valid": chain_valid,
                "failed_sequence": failed_sequence,
            },
            "capabilities": [item["name"] for item in self.registry.list()],
            "included": {
                "database": True,
                "portable_rag_database": bool(
                    self.settings.rag_database_path
                    and self.settings.rag_database_path.is_file()
                ),
                "generated_skill_source": True,
                "identity_and_lineage": True,
                "evolution_candidate_metadata": True,
                "autonomous_project_sources": True,
                "artifacts": include_artifacts,
                "model_weights": False,
                "secrets": False,
            },
            "restore_requirements": {
                "same_or_compatible_eck_version": True,
                "model_weights_reinstalled_separately": True,
                "secrets_reentered_by_owner": True,
                "restore_into_stopped_kernel": True,
                "post_restore_verification_required": True,
            },
            "files": files,
        }
