from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlparse

from eck import __version__
from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import RuntimeSkillManifest, RuntimeSkillRecord
from eck.events.bus import EventBus
from eck.experimental.p7.evolution_packs import DATA_PACK_TYPES, EvolutionDataPackService
from eck.experimental.p7.federation_registry import CapabilityRegistryService, CosignBlobService
from eck.services.project_lab import AutonomousProjectLabService
from eck.services.skill_forge import SkillForgeService
from eck.storage.sqlite import SQLiteStore


class FederationService:
    _format = "eck-evolution-pack.v1"
    _allowed_skill_suffixes = {".py", ".json", ".toml", ".md", ".txt", ".yaml", ".yml"}
    _forbidden_names = {".env", "soul.md", "eck.db", "credentials.json", "secrets.json"}
    _secret_patterns = (
        re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*['\"][^'\"]{6,}"),
        re.compile(r"(?i)[a-z]:\\users\\[^\\\s]+\\"),
        re.compile(r"/home/[^/\s]+/"),
    )

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        forge: SkillForgeService,
        project_lab: AutonomousProjectLabService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.forge = forge
        self.project_lab = project_lab
        self.root = (settings.workspace_dir / "federation").resolve()
        self.outbox = self.root / "outbox"
        self.inbox = self.root / "inbox"
        self.quarantine = self.root / "quarantine"
        self.installed = self.root / "installed"
        for path in (self.outbox, self.inbox, self.quarantine, self.installed):
            path.mkdir(parents=True, exist_ok=True)
        self.data_packs = EvolutionDataPackService(store)
        self.cosign = CosignBlobService(settings)
        self.capability_registry = CapabilityRegistryService(
            settings,
            self.root / "registry",
            project_lab,
        )
        self._ensure_installed_index()

    async def export_skill(
        self,
        runtime_skill_id: str,
        *,
        license_spdx: str,
        source_url: str = "",
    ) -> dict[str, Any]:
        if not self.settings.federation_enabled:
            raise RuntimeError("ECK Federation is disabled.")
        skill = self.store.get_runtime_skill(runtime_skill_id)
        if skill.status is not RuntimeSkillStatus.ACTIVE:
            raise ValueError("Only an active, locally verified skill can become an Evolution Pack.")
        if not skill.test_report.get("success"):
            raise ValueError("The skill has no successful local validation report.")
        if not re.fullmatch(r"[A-Za-z0-9.-]{3,64}", license_spdx):
            raise ValueError("A valid SPDX-style license identifier is required.")
        if source_url:
            parsed_source = urlparse(source_url)
            sensitive_query = {
                key.casefold()
                for key, _ in parse_qsl(parsed_source.query, keep_blank_values=True)
            } & {"token", "key", "api_key", "apikey", "password", "secret"}
            if (
                parsed_source.scheme not in {"http", "https"}
                or not parsed_source.hostname
                or parsed_source.username
                or parsed_source.password
                or sensitive_query
            ):
                raise ValueError("Evolution Pack source URL is unsafe or contains credentials.")
        source = Path(skill.source_dir).resolve()
        if not source.is_dir():
            raise FileNotFoundError(source)
        pack_id = new_id("evolution-pack")
        stage = self.root / f".{pack_id}.tmp"
        archive = self.outbox / f"{pack_id}.zip"
        stage.mkdir(parents=True, exist_ok=False)
        try:
            payload = stage / "payload"
            payload.mkdir()
            (payload / "manifest.json").write_text(
                skill.manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )
            self._copy_public_skill_source(source, payload)
            files = self._file_inventory(stage, prefix="payload/")
            manifest = {
                "format": self._format,
                "pack_id": pack_id,
                "pack_type": "skill_pack",
                "created_at": utc_now().isoformat(),
                "eck_version": __version__,
                "capability": skill.manifest.model_dump(mode="json"),
                "license": license_spdx,
                "source_url": source_url,
                "lineage": {
                    "publisher_node_sha256": self._node_id(),
                    "runtime_skill_id_sha256": hashlib.sha256(
                        runtime_skill_id.encode("utf-8")
                    ).hexdigest(),
                    "parent_pack_ids": [],
                },
                "compatibility": {
                    "python": ">=3.11",
                    "base_model_sha256": None,
                    "tokenizer_sha256": None,
                    "hardware_acceleration_required": False,
                },
                "privacy": {
                    "soul": False,
                    "private_memory": False,
                    "owner_settings": False,
                    "credentials": False,
                    "machine_paths": False,
                },
                "reproductions": [
                    {
                        "node_sha256": self._node_id(),
                        "success": True,
                        "test_report_sha256": hashlib.sha256(
                            json.dumps(
                                skill.test_report,
                                ensure_ascii=False,
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest(),
                    }
                ],
                "signature": {
                    "scheme": "none",
                    "verified": False,
                    "detail": (
                        "Hash-verified local pack; Registry publication still requires "
                        "Sigstore/Cosign."
                    ),
                },
                "files": files,
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            shutil.make_archive(str(archive.with_suffix("")), "zip", stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        verification = self.verify(archive.name, location="outbox")
        await self.events.publish(
            "EvolutionPackExported",
            pack_id,
            {
                "archive": archive.name,
                "pack_type": "skill_pack",
                "valid": verification["valid"],
            },
        )
        return {
            "pack_id": pack_id,
            "archive": archive.name,
            "download_url": f"/v1/federation/packs/{archive.name}",
            "verification": verification,
        }

    async def export_knowledge(
        self,
        run_ids: tuple[str, ...],
        *,
        license_spdx: str,
        source_url: str = "",
    ) -> dict[str, Any]:
        return await self._export_data_pack(
            self.data_packs.build_knowledge(run_ids),
            license_spdx=license_spdx,
            source_url=source_url,
        )

    async def export_strategy(
        self,
        mission_id: str,
        *,
        license_spdx: str,
        source_url: str = "",
    ) -> dict[str, Any]:
        return await self._export_data_pack(
            self.data_packs.build_strategy(mission_id),
            license_spdx=license_spdx,
            source_url=source_url,
        )

    async def export_evaluation(
        self,
        run_ids: tuple[str, ...],
        *,
        license_spdx: str,
        source_url: str = "",
    ) -> dict[str, Any]:
        return await self._export_data_pack(
            self.data_packs.build_evaluation(run_ids),
            license_spdx=license_spdx,
            source_url=source_url,
        )

    async def export_distillation(
        self,
        mission_ids: tuple[str, ...],
        *,
        license_spdx: str,
        source_url: str = "",
    ) -> dict[str, Any]:
        return await self._export_data_pack(
            self.data_packs.build_distillation(mission_ids),
            license_spdx=license_spdx,
            source_url=source_url,
        )

    async def _export_data_pack(
        self,
        built: dict[str, Any],
        *,
        license_spdx: str,
        source_url: str,
    ) -> dict[str, Any]:
        if not self.settings.federation_enabled:
            raise RuntimeError("ECK Federation is disabled.")
        self._validate_export_metadata(license_spdx, source_url)
        pack_type = str(built["pack_type"])
        if pack_type not in DATA_PACK_TYPES:
            raise ValueError("Unsupported Evolution Pack type.")
        pack_id = new_id("evolution-pack")
        stage = self.root / f".{pack_id}.tmp"
        archive = self.outbox / f"{pack_id}.zip"
        stage.mkdir(parents=True, exist_ok=False)
        try:
            payload = stage / "payload"
            payload.mkdir()
            for name, content in built["payloads"].items():
                self._validate_public_path(f"payload/{name}")
                self._scan_public_text(f"payload/{name}", content)
                destination = payload / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            files = self._file_inventory(stage, prefix="payload/")
            source_refs = [
                hashlib.sha256(str(item).encode("utf-8")).hexdigest()
                for item in built["source_refs"]
            ]
            manifest = {
                "format": self._format,
                "pack_id": pack_id,
                "pack_type": pack_type,
                "created_at": utc_now().isoformat(),
                "eck_version": __version__,
                "capability": built["capability"],
                "license": license_spdx,
                "source_url": source_url,
                "lineage": {
                    "publisher_node_sha256": self._node_id(),
                    "source_record_sha256": source_refs,
                    "parent_pack_ids": [],
                },
                "compatibility": {
                    "format": "json" if len(files) == 1 else "json+jsonl",
                    "base_model_sha256": None,
                    "tokenizer_sha256": None,
                    "hardware_acceleration_required": False,
                },
                "privacy": {
                    "soul": False,
                    "private_memory": False,
                    "owner_settings": False,
                    "credentials": False,
                    "machine_paths": False,
                },
                "reproductions": [
                    {
                        "node_sha256": self._node_id(),
                        "success": True,
                        "test_report_sha256": self._json_digest(built["metrics"]),
                    }
                ],
                "signature": {
                    "scheme": "detached-sigstore-bundle",
                    "verified": False,
                    "detail": "Use Cosign before Registry admission.",
                },
                "files": files,
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            shutil.make_archive(str(archive.with_suffix("")), "zip", stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        verification = self.verify(archive.name, location="outbox")
        await self.events.publish(
            "EvolutionPackExported",
            pack_id,
            {"archive": archive.name, "pack_type": pack_type, "valid": verification["valid"]},
        )
        return {
            "pack_id": pack_id,
            "archive": archive.name,
            "pack_type": pack_type,
            "download_url": f"/v1/federation/packs/{archive.name}",
            "verification": verification,
        }

    def verify(self, archive_name: str, *, location: str = "inbox") -> dict[str, Any]:
        archive = self.pack_path(archive_name, location=location)
        return self._verify_archive(archive)

    def _verify_archive(self, archive: Path) -> dict[str, Any]:
        max_bytes = self.settings.federation_max_pack_mb * 1024**2
        failures: list[str] = []
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            names = {item.filename for item in infos}
            if "manifest.json" not in names:
                raise ValueError("Evolution Pack has no manifest.json.")
            total_uncompressed = 0
            for info in infos:
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                    raise ValueError("Evolution Pack contains an unsafe path.")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Evolution Pack contains a symbolic link.")
                total_uncompressed += info.file_size
            if total_uncompressed > max_bytes:
                failures.append("uncompressed-size-limit")
            manifest = self._manifest(bundle)
            if manifest.get("format") != self._format:
                failures.append("unsupported-format")
            pack_type = str(manifest.get("pack_type", ""))
            if pack_type not in {"skill_pack", *DATA_PACK_TYPES}:
                failures.append("unsupported-pack-type")
            files = manifest.get("files")
            if not isinstance(files, dict):
                raise ValueError("Evolution Pack file inventory is invalid.")
            if len(files) > 2000:
                failures.append("file-count-limit")
            for name, expected_hash in files.items():
                if name not in names:
                    failures.append(f"missing:{name}")
                    continue
                if hashlib.sha256(bundle.read(name)).hexdigest() != expected_hash:
                    failures.append(f"sha256:{name}")
            unexpected = sorted(
                name
                for name in names
                if name != "manifest.json" and not name.endswith("/") and name not in files
            )
            failures.extend(f"untracked:{name}" for name in unexpected)
            for name in files:
                self._validate_public_path(name)
                self._scan_public_text(name, bundle.read(name))
            privacy = manifest.get("privacy", {})
            if not isinstance(privacy, dict) or any(bool(value) for value in privacy.values()):
                failures.append("privacy-declaration")
        signature_declaration = manifest.get("signature", {})
        if not isinstance(signature_declaration, dict):
            failures.append("signature-declaration")
        signature = self.cosign.verify(archive)
        return {
            "valid": not failures,
            "archive": archive.name,
            "format": manifest.get("format"),
            "pack_id": manifest.get("pack_id"),
            "pack_type": manifest.get("pack_type"),
            "files_checked": len(files),
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "signature_verified": bool(signature["verified"]),
            "signature": signature,
            "failures": failures,
        }

    def preview(self, archive_name: str, *, location: str = "inbox") -> dict[str, Any]:
        verification = self.verify(archive_name, location=location)
        if not verification["valid"]:
            raise ValueError("Evolution Pack verification failed.")
        archive = self.pack_path(archive_name, location=location)
        with zipfile.ZipFile(archive) as bundle:
            manifest = self._manifest(bundle)
        pack_type = str(manifest["pack_type"])
        successful_nodes = {
            str(item.get("node_sha256"))
            for item in manifest.get("reproductions", [])
            if isinstance(item, dict) and item.get("success") and item.get("node_sha256")
        }
        common = {
            "archive_sha256": verification["archive_sha256"],
            "pack_id": verification["pack_id"],
            "pack_type": pack_type,
            "private_layers_touched": [],
            "quarantine_required": True,
            "publisher_reproductions": len(successful_nodes),
            "required_reproductions": self.settings.federation_min_reproductions,
            "signature_verified": verification["signature_verified"],
        }
        if pack_type == "skill_pack":
            capability = RuntimeSkillManifest.model_validate(manifest["capability"])
            existing = [
                item
                for item in self.store.list_runtime_skills(limit=10000)
                if item.manifest.name == capability.name
            ]
            plan = {
                **common,
                "capability": capability.model_dump(mode="json"),
                "existing_versions": sorted({item.manifest.version for item in existing}),
                "would_replace_active": any(
                    item.status is RuntimeSkillStatus.ACTIVE for item in existing
                ),
            }
        else:
            capability = manifest.get("capability", {})
            installed = self._read_installed_index()
            plan = {
                **common,
                "capability": capability,
                "already_installed": any(
                    item.get("pack_id") == verification["pack_id"]
                    for item in installed["items"]
                ),
                "would_replace_active": False,
                "activation_scope": "federated learning library",
            }
        return {
            "verification": verification,
            "plan": plan,
            "plan_sha256": self._json_digest(plan),
            "activation_allowed": False,
            "detail": "Preview never modifies SOUL, memory, settings, skills, or model weights.",
        }

    async def stage(self, archive_name: str, *, plan_sha256: str) -> dict[str, Any]:
        preview = self.preview(archive_name)
        if preview["plan_sha256"] != plan_sha256:
            raise ValueError("Evolution Pack changed after preview; create a new diff plan.")
        pack_id = str(preview["plan"]["pack_id"])
        target = (self.quarantine / pack_id).resolve()
        target.relative_to(self.quarantine)
        if target.exists():
            raise FileExistsError(f"Evolution Pack is already staged: {pack_id}")
        archive = self.pack_path(archive_name)
        target.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                for info in bundle.infolist():
                    if info.is_dir():
                        continue
                    relative = Path(*PurePosixPath(info.filename).parts)
                    destination = (target / relative).resolve()
                    destination.relative_to(target)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(bundle.read(info.filename))
            (target / "stage.json").write_text(
                json.dumps(
                    {
                        "archive": archive_name,
                        "plan_sha256": plan_sha256,
                        "staged_at": utc_now().isoformat(),
                        "installed": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        await self.events.publish(
            "EvolutionPackQuarantined",
            pack_id,
            {"archive": archive_name, "plan_sha256": plan_sha256},
        )
        return {
            "pack_id": pack_id,
            "status": "quarantined",
            "path": str(target),
            "next": "Run isolated reproduction before installation.",
        }

    async def reproduce(self, pack_id: str) -> dict[str, Any]:
        target, manifest = self._staged_pack(pack_id)
        pack_type = str(manifest["pack_type"])
        if pack_type == "skill_pack":
            capability = RuntimeSkillManifest.model_validate(manifest["capability"])
            now = utc_now()
            transient = RuntimeSkillRecord(
                runtime_skill_id=f"federation-{pack_id}",
                manifest=capability,
                status=RuntimeSkillStatus.TESTING,
                source_dir=str(target / "payload"),
                source="federation",
                test_report={},
                improvements=("Imported Evolution Pack isolated reproduction.",),
                created_at=now,
                updated_at=now,
            )
            report = await self.forge.worker.validate(transient)
        else:
            report = self.data_packs.reproduce(pack_type, target / "payload")
        reproduction = {
            "node_sha256": self._node_id(),
            "success": bool(report.get("success")),
            "worker_unavailable": bool(report.get("worker_unavailable")),
            "report_sha256": self._json_digest(report),
            "completed_at": utc_now().isoformat(),
            "detail": str(report.get("detail", ""))[-2000:],
            "metrics": report.get("metrics", {}),
        }
        (target / "local-reproduction.json").write_text(
            json.dumps(reproduction, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await self.events.publish(
            "EvolutionPackReproduced" if reproduction["success"] else "EvolutionPackRejected",
            pack_id,
            reproduction,
        )
        return reproduction

    async def install(self, pack_id: str) -> dict[str, Any]:
        target, manifest = self._staged_pack(pack_id)
        reproduction_path = target / "local-reproduction.json"
        if not reproduction_path.is_file():
            raise ValueError("Local isolated reproduction has not run.")
        local = json.loads(reproduction_path.read_text(encoding="utf-8"))
        if not local.get("success"):
            raise ValueError("Local isolated reproduction did not pass.")
        successful_nodes = {
            str(item.get("node_sha256"))
            for item in manifest.get("reproductions", [])
            if isinstance(item, dict) and item.get("success") and item.get("node_sha256")
        }
        successful_nodes.add(str(local["node_sha256"]))
        if len(successful_nodes) < self.settings.federation_min_reproductions:
            raise ValueError("Evolution Pack has not passed enough independent reproductions.")
        if manifest["pack_type"] != "skill_pack":
            return await self._install_data_pack(
                pack_id,
                target,
                manifest,
                reproduction_count=len(successful_nodes),
            )
        capability = RuntimeSkillManifest.model_validate(manifest["capability"])
        if any(
            item.manifest.name == capability.name
            and item.manifest.version == capability.version
            for item in self.store.list_runtime_skills(limit=10000)
        ):
            raise ValueError("The same capability version already exists locally.")
        destination = (
            self.settings.workspace_dir
            / "runtime_skills"
            / capability.name
            / f"{capability.version}-federated-{pack_id[-8:]}"
        ).resolve()
        destination.relative_to(self.settings.workspace_dir.resolve())
        shutil.copytree(target / "payload", destination)
        skill = self.store.add_runtime_skill(
            capability,
            source_dir=str(destination),
            source="federation",
            status=RuntimeSkillStatus.DRAFT,
            test_report={"federation_reproductions": len(successful_nodes)},
            improvements=(f"Adopted from Evolution Pack {pack_id}.",),
        )
        validation = await self.forge.validate_skill(skill.runtime_skill_id)
        stage = json.loads((target / "stage.json").read_text(encoding="utf-8"))
        stage.update(
            {
                "installed": True,
                "runtime_skill_id": skill.runtime_skill_id,
                "installed_at": utc_now().isoformat(),
            }
        )
        (target / "stage.json").write_text(
            json.dumps(stage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await self.events.publish(
            "EvolutionPackInstalled",
            pack_id,
            {
                "runtime_skill_id": skill.runtime_skill_id,
                "status": validation.get("status"),
                "reproductions": len(successful_nodes),
            },
        )
        return {
            "pack_id": pack_id,
            "runtime_skill_id": skill.runtime_skill_id,
            "reproductions": len(successful_nodes),
            "validation": validation,
        }

    async def _install_data_pack(
        self,
        pack_id: str,
        target: Path,
        manifest: dict[str, Any],
        *,
        reproduction_count: int,
    ) -> dict[str, Any]:
        index = self._read_installed_index()
        if any(item.get("pack_id") == pack_id for item in index["items"]):
            raise ValueError("The same Evolution Pack is already installed locally.")
        destination = (self.installed / pack_id).resolve()
        destination.relative_to(self.installed.resolve())
        if destination.exists():
            raise FileExistsError(pack_id)
        destination.mkdir(parents=True)
        try:
            shutil.copytree(target / "payload", destination / "payload")
            shutil.copy2(target / "manifest.json", destination / "manifest.json")
            reproduction_path = target / "local-reproduction.json"
            shutil.copy2(reproduction_path, destination / reproduction_path.name)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        record = {
            "pack_id": pack_id,
            "pack_type": manifest["pack_type"],
            "capability": manifest.get("capability", {}),
            "source_url": manifest.get("source_url", ""),
            "reproductions": reproduction_count,
            "trust_basis": "publisher-plus-local-reproduction",
            "installed_at": utc_now().isoformat(),
            "path": str(destination),
            "active": True,
        }
        index["items"].append(record)
        index["updated_at"] = utc_now().isoformat()
        self._write_json_atomic(self.installed / "index.json", index)
        stage = json.loads((target / "stage.json").read_text(encoding="utf-8"))
        stage.update({"installed": True, "installed_at": record["installed_at"]})
        self._write_json_atomic(target / "stage.json", stage)
        synthesis = self.synthesis_status()
        await self.events.publish(
            "EvolutionPackInstalled",
            pack_id,
            {
                "pack_type": manifest["pack_type"],
                "reproductions": reproduction_count,
                "synthesis_status": synthesis["status"],
            },
        )
        return {
            "pack_id": pack_id,
            "pack_type": manifest["pack_type"],
            "reproductions": reproduction_count,
            "library_record": record,
            "synthesis": synthesis,
        }

    def sign(self, archive_name: str) -> dict[str, Any]:
        archive = self.pack_path(archive_name, location="outbox")
        result = self.cosign.sign(archive)
        return {"archive": archive.name, **result}

    def submit_registry_candidate(self, archive_name: str) -> dict[str, Any]:
        archive = self.pack_path(archive_name, location="outbox")
        verification = self._verify_archive(archive)
        with zipfile.ZipFile(archive) as bundle:
            manifest = self._manifest(bundle)
        bundle_path = self.cosign.bundle_path(archive)
        return self.capability_registry.submit(
            archive,
            verification=verification,
            manifest=manifest,
            signature_bundle=bundle_path if bundle_path.is_file() else None,
        )

    def review_registry_candidate(
        self,
        pack_id: str,
        *,
        reviewer_node_sha256: str,
        verdict: str,
        reproduction_success: bool,
        fixed_test_delta: float,
        hidden_test_regression: bool,
        permission_reviewed: bool,
        dependency_reviewed: bool,
        evidence_sha256: str,
        notes: str,
    ) -> dict[str, Any]:
        return self.capability_registry.add_review(
            pack_id,
            reviewer_node_sha256=reviewer_node_sha256,
            verdict=verdict,
            reproduction_success=reproduction_success,
            fixed_test_delta=fixed_test_delta,
            hidden_test_regression=hidden_test_regression,
            permission_reviewed=permission_reviewed,
            dependency_reviewed=dependency_reviewed,
            evidence_sha256=evidence_sha256,
            notes=notes,
        )

    def admit_registry_candidate(self, pack_id: str) -> dict[str, Any]:
        candidate = self.capability_registry.candidate(pack_id)
        candidate_dir = self.capability_registry.candidates / pack_id
        archive = candidate_dir / str(candidate["archive"])
        verification = self._verify_archive(archive)
        if not verification["valid"]:
            raise ValueError("Registry candidate archive no longer passes integrity checks.")
        return self.capability_registry.admit(
            pack_id,
            signature_verified=verification["signature_verified"],
        )

    def revoke_registry_pack(self, pack_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("Registry revocation requires a reason.")
        return self.capability_registry.revoke(pack_id, reason=reason)

    async def publish_registry(self) -> dict[str, Any]:
        return await self.capability_registry.publish()

    def synthesis_status(self) -> dict[str, Any]:
        index = self._read_installed_index()
        active = [item for item in index["items"] if item.get("active")]
        data = [item for item in active if item.get("pack_type") in DATA_PACK_TYPES]
        pack_types = {str(item["pack_type"]) for item in data}
        publishers: set[str] = set()
        evaluation_evidence = False
        topics: set[str] = set()
        for item in data:
            manifest_path = Path(str(item["path"])) / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                publisher = manifest.get("lineage", {}).get("publisher_node_sha256")
                if publisher:
                    publishers.add(str(publisher))
            capability = item.get("capability", {})
            topics.update(str(tag).casefold() for tag in capability.get("tags", []))
            if item.get("pack_type") == "evaluation_pack":
                payload = Path(str(item["path"])) / "payload" / "evaluation.json"
                if payload.is_file():
                    value = json.loads(payload.read_text(encoding="utf-8"))
                    quality = value.get("quality", {})
                    evaluation_evidence = evaluation_evidence or (
                        int(quality.get("total_samples", 0)) > 0
                        and not quality.get("hidden_answers_included", True)
                        and bool(quality.get("improvement_verified"))
                    )
        quantity_ready = len(data) >= self.settings.federation_synthesis_min_packs
        diversity_ready = len(pack_types) >= self.settings.federation_synthesis_min_types
        if quantity_ready and diversity_ready and evaluation_evidence:
            status = "verified-cross-pack-synthesis-ready"
        elif quantity_ready and diversity_ready:
            status = "evaluation-required"
        else:
            status = "collecting-complementary-evidence"
        return {
            "status": status,
            "installed_data_packs": len(data),
            "pack_types": sorted(pack_types),
            "publisher_nodes": len(publishers),
            "topic_tags": sorted(topics)[:100],
            "quantity_threshold": self.settings.federation_synthesis_min_packs,
            "type_threshold": self.settings.federation_synthesis_min_types,
            "evaluation_evidence": evaluation_evidence,
            "claim": (
                "Quantity is considered capability growth only when complementary pack types "
                "are locally reproduced and Evaluation Packs show comparable measured improvement."
            ),
        }

    def learning_context(
        self,
        query: str,
        *,
        project_type: str = "general",
        limit: int = 8,
    ) -> dict[str, list[dict[str, Any]]]:
        tokens = set(re.findall(r"[a-z][a-z0-9-]{2,}|[\u4e00-\u9fff]{2,8}", query.casefold()))
        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in self._read_installed_index()["items"]:
            if not item.get("active") or item.get("pack_type") not in DATA_PACK_TYPES:
                continue
            capability = item.get("capability", {})
            text = " ".join(
                [
                    str(capability.get("name", "")),
                    str(capability.get("description", "")),
                    " ".join(str(tag) for tag in capability.get("tags", [])),
                    project_type,
                ]
            ).casefold()
            score = sum(1 for token in tokens if token in text)
            ranked.append((score, item))
        ranked.sort(
            key=lambda entry: (entry[0], str(entry[1].get("installed_at", ""))),
            reverse=True,
        )
        context: dict[str, list[dict[str, Any]]] = {
            "knowledge": [],
            "strategies": [],
            "evaluations": [],
            "distillation_examples": [],
            "provenance": [],
        }
        for _, item in ranked[:limit]:
            root = Path(str(item["path"])) / "payload"
            pack_type = item["pack_type"]
            context["provenance"].append(
                {
                    "pack_id": item["pack_id"],
                    "pack_type": pack_type,
                    "reproductions": item.get("reproductions", 0),
                    "trust_basis": item.get("trust_basis", ""),
                }
            )
            if pack_type == "knowledge_pack":
                payload = json.loads((root / "knowledge.json").read_text(encoding="utf-8"))
                context["knowledge"].extend(payload.get("runs", [])[:2])
            elif pack_type == "strategy_pack":
                payload = json.loads((root / "strategy.json").read_text(encoding="utf-8"))
                context["strategies"].append(payload)
            elif pack_type == "evaluation_pack":
                payload = json.loads((root / "evaluation.json").read_text(encoding="utf-8"))
                context["evaluations"].extend(payload.get("runs", [])[:3])
            elif pack_type == "distillation_pack":
                lines = (root / "distillation.jsonl").read_text(encoding="utf-8").splitlines()
                context["distillation_examples"].extend(
                    json.loads(line) for line in lines[:3] if line.strip()
                )
        return {key: values[:limit] for key, values in context.items()}

    def pack_path(self, archive_name: str, *, location: str = "inbox") -> Path:
        safe_name = Path(archive_name).name
        if safe_name != archive_name or not safe_name.endswith(".zip"):
            raise ValueError("Invalid Evolution Pack name.")
        roots = {"inbox": self.inbox, "outbox": self.outbox}
        if location not in roots:
            raise ValueError("Invalid Evolution Pack location.")
        archive = (roots[location] / safe_name).resolve()
        archive.relative_to(roots[location])
        if not archive.is_file():
            raise FileNotFoundError(safe_name)
        return archive

    def _staged_pack(self, pack_id: str) -> tuple[Path, dict[str, Any]]:
        if not re.fullmatch(r"evolution-pack_[a-f0-9]{32}", pack_id):
            raise ValueError("Invalid Evolution Pack ID.")
        target = (self.quarantine / pack_id).resolve()
        target.relative_to(self.quarantine)
        manifest_path = target / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(pack_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != self._format or manifest.get("pack_id") != pack_id:
            raise ValueError("Staged Evolution Pack manifest is invalid.")
        return target, manifest

    def _copy_public_skill_source(self, source: Path, target: Path) -> None:
        copied = 0
        for path in sorted(source.rglob("*")):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(source)
            if path.suffix.casefold() not in self._allowed_skill_suffixes:
                raise ValueError(f"Unsupported Skill Pack file: {relative.as_posix()}")
            self._validate_public_path(relative.as_posix())
            content = path.read_bytes()
            self._scan_public_text(relative.as_posix(), content)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            copied += 1
        if copied == 0:
            raise ValueError("Skill Pack source is empty.")

    @staticmethod
    def _validate_export_metadata(license_spdx: str, source_url: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9.-]{3,64}", license_spdx):
            raise ValueError("A valid SPDX-style license identifier is required.")
        if not source_url:
            return
        parsed_source = urlparse(source_url)
        sensitive_query = {
            key.casefold() for key, _ in parse_qsl(parsed_source.query, keep_blank_values=True)
        } & {"token", "key", "api_key", "apikey", "password", "secret"}
        if (
            parsed_source.scheme not in {"http", "https"}
            or not parsed_source.hostname
            or parsed_source.username
            or parsed_source.password
            or sensitive_query
        ):
            raise ValueError("Evolution Pack source URL is unsafe or contains credentials.")

    def _validate_public_path(self, name: str) -> None:
        path = PurePosixPath(name)
        if path.name.casefold() in self._forbidden_names or any(
            part.casefold() in {"identity", "private", "credentials", "secrets"}
            for part in path.parts
        ):
            raise ValueError(f"Private file is forbidden in Evolution Pack: {name}")

    def _scan_public_text(self, name: str, content: bytes) -> None:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Evolution Pack files must be auditable UTF-8 text: {name}") from exc
        if any(pattern.search(text) for pattern in self._secret_patterns):
            raise ValueError(f"Potential secret or machine path found in Evolution Pack: {name}")

    def _ensure_installed_index(self) -> None:
        path = self.installed / "index.json"
        if not path.is_file():
            self._write_json_atomic(
                path,
                {"schema": "eck-federated-learning-library.v1", "items": []},
            )

    def _read_installed_index(self) -> dict[str, Any]:
        path = self.installed / "index.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schema") != "eck-federated-learning-library.v1"
            or not isinstance(value.get("items"), list)
        ):
            raise ValueError("Federated learning library index is invalid.")
        return value

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _manifest(bundle: zipfile.ZipFile) -> dict[str, Any]:
        value = json.loads(bundle.read("manifest.json"))
        if not isinstance(value, dict):
            raise ValueError("Evolution Pack manifest must be an object.")
        return value

    @staticmethod
    def _file_inventory(root: Path, *, prefix: str) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).as_posix().startswith(prefix)
        }

    def _node_id(self) -> str:
        return hashlib.sha256(self.settings.identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.federation_enabled,
            "format": self._format,
            "node_sha256": self._node_id(),
            "platform": platform.system(),
            "minimum_reproductions": self.settings.federation_min_reproductions,
            "outbox": len(list(self.outbox.glob("*.zip"))),
            "inbox": len(list(self.inbox.glob("*.zip"))),
            "quarantined": len([item for item in self.quarantine.iterdir() if item.is_dir()]),
            "installed_data_packs": len(self._read_installed_index()["items"]),
            "cosign": self.cosign.status(),
            "registry": self.capability_registry.status(),
            "synthesis": self.synthesis_status(),
            "private_layers_shared": False,
            "model_weights_shared": False,
        }
