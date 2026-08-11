from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.services.project_lab import AutonomousProjectLabService


class CosignBlobService:
    """Detached Sigstore bundle support without placing signing keys in ECK packs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        executable = self._executable()
        return {
            "enabled": self.settings.federation_cosign_enabled,
            "installed": executable is not None,
            "executable": executable,
            "signing_key_configured": bool(self.settings.federation_cosign_key_path),
            "public_key_configured": bool(self.settings.federation_cosign_public_key_path),
            "identity_policy_configured": bool(
                self.settings.federation_cosign_certificate_identity
                and self.settings.federation_cosign_oidc_issuer
            ),
        }

    def sign(self, archive: Path) -> dict[str, Any]:
        executable = self._require_executable()
        key = self.settings.federation_cosign_key_path
        if key is None or not key.is_file():
            raise RuntimeError(
                "Unattended Cosign signing requires ECK_FEDERATION_COSIGN_KEY_PATH. "
                "Keyless OIDC signing is intentionally not started from the local daemon."
            )
        bundle = self.bundle_path(archive)
        result = self._run(
            [
                executable,
                "sign-blob",
                "--yes",
                "--key",
                str(key),
                "--bundle",
                str(bundle),
                str(archive),
            ]
        )
        if result["returncode"] != 0:
            bundle.unlink(missing_ok=True)
            raise RuntimeError(f"Cosign signing failed: {result['detail']}")
        verification = self.verify(archive)
        if not verification["verified"]:
            bundle.unlink(missing_ok=True)
            raise RuntimeError(f"Cosign produced an unverifiable bundle: {verification['detail']}")
        return {"bundle": bundle.name, **verification}

    def verify(self, archive: Path) -> dict[str, Any]:
        bundle = self.bundle_path(archive)
        if not bundle.is_file():
            return {
                "verified": False,
                "scheme": "sigstore-cosign",
                "bundle": None,
                "detail": "Detached Sigstore bundle is missing.",
            }
        executable = self._executable()
        if executable is None:
            return {
                "verified": False,
                "scheme": "sigstore-cosign",
                "bundle": bundle.name,
                "detail": "Cosign is not installed; bundle cannot be cryptographically verified.",
            }
        command = [executable, "verify-blob", "--bundle", str(bundle)]
        public_key = self.settings.federation_cosign_public_key_path
        identity = self.settings.federation_cosign_certificate_identity
        issuer = self.settings.federation_cosign_oidc_issuer
        if public_key is not None and public_key.is_file():
            command.extend(["--key", str(public_key)])
        elif identity and issuer:
            command.extend(
                [
                    "--certificate-identity",
                    identity,
                    "--certificate-oidc-issuer",
                    issuer,
                ]
            )
        else:
            return {
                "verified": False,
                "scheme": "sigstore-cosign",
                "bundle": bundle.name,
                "detail": "No trusted public key or certificate identity policy is configured.",
            }
        command.append(str(archive))
        result = self._run(command)
        return {
            "verified": result["returncode"] == 0,
            "scheme": "sigstore-cosign",
            "bundle": bundle.name,
            "detail": result["detail"],
        }

    @staticmethod
    def bundle_path(archive: Path) -> Path:
        return archive.with_name(f"{archive.name}.sigstore.json")

    def _executable(self) -> str | None:
        if not self.settings.federation_cosign_enabled:
            return None
        configured = self.settings.federation_cosign_executable
        if configured:
            resolved = shutil.which(configured)
            if resolved:
                return resolved
            path = Path(configured)
            if path.is_file():
                return str(path.resolve())
        return shutil.which("cosign")

    def _require_executable(self) -> str:
        executable = self._executable()
        if executable is None:
            raise RuntimeError("Cosign is not installed or ECK Federation signing is disabled.")
        return executable

    @staticmethod
    def _run(command: list[str]) -> dict[str, Any]:
        environment = os.environ.copy()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env=environment,
        )
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        return {"returncode": result.returncode, "detail": detail[-4000:]}


class CapabilityRegistryService:
    """File-backed candidate review, admission, revocation, and GitHub publication."""

    def __init__(
        self,
        settings: Settings,
        root: Path,
        project_lab: AutonomousProjectLabService,
    ) -> None:
        self.settings = settings
        self.root = root.resolve()
        self.project_lab = project_lab
        self.candidates = self.root / "candidates"
        self.public = self.root / "public"
        self.packs = self.public / "packs"
        self.metadata = self.public / "metadata"
        for path in (self.candidates, self.packs, self.metadata):
            path.mkdir(parents=True, exist_ok=True)
        self._ensure_public_files()

    def submit(
        self,
        archive: Path,
        *,
        verification: dict[str, Any],
        manifest: dict[str, Any],
        signature_bundle: Path | None,
    ) -> dict[str, Any]:
        if not verification.get("valid"):
            raise ValueError("Only a hash-valid Evolution Pack can enter Registry review.")
        pack_id = str(verification.get("pack_id", ""))
        target = self._candidate_dir(pack_id)
        target.mkdir(parents=True, exist_ok=True)
        copied_archive = target / archive.name
        shutil.copy2(archive, copied_archive)
        copied_bundle: str | None = None
        if signature_bundle is not None and signature_bundle.is_file():
            destination = target / signature_bundle.name
            shutil.copy2(signature_bundle, destination)
            copied_bundle = destination.name
        publisher_nodes = sorted(
            {
                str(item.get("node_sha256"))
                for item in manifest.get("reproductions", [])
                if isinstance(item, dict) and item.get("success") and item.get("node_sha256")
            }
        )
        entry = {
            "schema": "eck-registry-candidate.v1",
            "pack_id": pack_id,
            "pack_type": verification.get("pack_type"),
            "archive": copied_archive.name,
            "archive_sha256": verification.get("archive_sha256"),
            "signature_bundle": copied_bundle,
            "signature_verified": bool(verification.get("signature_verified")),
            "publisher_reproduction_nodes": publisher_nodes,
            "submitted_at": utc_now().isoformat(),
            "status": "under_review",
        }
        self._write_json(target / "entry.json", entry)
        return self.candidate(pack_id)

    def add_review(
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
        target = self._candidate_dir(pack_id)
        self._read_json(target / "entry.json")
        if not re.fullmatch(r"[a-f0-9]{64}", reviewer_node_sha256):
            raise ValueError("Reviewer node ID must be a SHA-256 digest.")
        if verdict not in {"approve", "reject"}:
            raise ValueError("Registry review verdict must be approve or reject.")
        if not re.fullmatch(r"[a-f0-9]{64}", evidence_sha256):
            raise ValueError("Review evidence must be identified by SHA-256.")
        reviews_dir = target / "reviews"
        reviews_dir.mkdir(exist_ok=True)
        review = {
            "schema": "eck-community-review.v1",
            "review_id": new_id("federation-review"),
            "pack_id": pack_id,
            "reviewer_node_sha256": reviewer_node_sha256,
            "verdict": verdict,
            "reproduction_success": reproduction_success,
            "fixed_test_delta": fixed_test_delta,
            "hidden_test_regression": hidden_test_regression,
            "permission_reviewed": permission_reviewed,
            "dependency_reviewed": dependency_reviewed,
            "evidence_sha256": evidence_sha256,
            "notes": notes[:2000],
            "created_at": utc_now().isoformat(),
        }
        self._write_json(reviews_dir / f"review-{reviewer_node_sha256[:20]}.json", review)
        return self.candidate(pack_id)

    def candidate(self, pack_id: str) -> dict[str, Any]:
        target = self._candidate_dir(pack_id)
        entry = self._read_json(target / "entry.json")
        reviews = self._reviews(target)
        trust = self._trust(entry, reviews)
        return {**entry, "reviews": reviews, "trust": trust}

    def admit(self, pack_id: str, *, signature_verified: bool) -> dict[str, Any]:
        target = self._candidate_dir(pack_id)
        entry = self._read_json(target / "entry.json")
        entry["signature_verified"] = signature_verified
        reviews = self._reviews(target)
        trust = self._trust(entry, reviews)
        if not trust["admission_allowed"]:
            raise ValueError("Registry trust and independent review thresholds are not satisfied.")
        archive = target / str(entry["archive"])
        destination = self.packs / archive.name
        shutil.copy2(archive, destination)
        bundle_name = entry.get("signature_bundle")
        if bundle_name:
            shutil.copy2(target / str(bundle_name), self.packs / str(bundle_name))
        admitted = {
            **entry,
            "status": "admitted",
            "admitted_at": utc_now().isoformat(),
            "trust": trust,
            "reviews": reviews,
            "download_path": f"packs/{archive.name}",
        }
        self._write_json(self.metadata / f"{pack_id}.json", admitted)
        self._update_index(admitted)
        entry.update({"status": "admitted", "admitted_at": admitted["admitted_at"]})
        self._write_json(target / "entry.json", entry)
        return admitted

    def revoke(self, pack_id: str, *, reason: str) -> dict[str, Any]:
        metadata_path = self.metadata / f"{self._safe_pack_id(pack_id)}.json"
        record = self._read_json(metadata_path)
        record.update(
            {
                "status": "revoked",
                "revoked_at": utc_now().isoformat(),
                "revocation_reason": reason[:2000],
            }
        )
        self._write_json(metadata_path, record)
        self._update_index(record)
        revocations = self._read_json(self.public / "revocations.json")
        items = [item for item in revocations["items"] if item.get("pack_id") != pack_id]
        items.append(
            {
                "pack_id": pack_id,
                "reason": reason[:2000],
                "revoked_at": record["revoked_at"],
            }
        )
        revocations["items"] = sorted(items, key=lambda item: str(item["pack_id"]))
        self._write_json(self.public / "revocations.json", revocations)
        return record

    async def publish(self) -> dict[str, Any]:
        self._write_readme()
        return await self.project_lab.publish_directory(
            name="eck-capability-registry",
            source_dir=self.public,
            visibility="public",
        )

    def status(self) -> dict[str, Any]:
        index = self._read_json(self.public / "index.json")
        candidates = [item for item in self.candidates.iterdir() if item.is_dir()]
        admitted = [item for item in index["items"] if item.get("status") == "admitted"]
        revoked = [item for item in index["items"] if item.get("status") == "revoked"]
        return {
            "format": "eck-capability-registry.v1",
            "candidates": len(candidates),
            "admitted": len(admitted),
            "revoked": len(revoked),
            "minimum_reviews": self.settings.federation_registry_min_reviews,
            "minimum_trust_score": self.settings.federation_registry_min_trust_score,
            "public_directory": str(self.public),
        }

    def _trust(self, entry: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
        approvals = [review for review in reviews if review.get("verdict") == "approve"]
        rejections = [review for review in reviews if review.get("verdict") == "reject"]
        reproductions = set(entry.get("publisher_reproduction_nodes", []))
        reproductions.update(
            str(review["reviewer_node_sha256"])
            for review in approvals
            if review.get("reproduction_success")
        )
        enough_reviews = len(approvals) >= self.settings.federation_registry_min_reviews
        enough_reproductions = len(reproductions) >= self.settings.federation_min_reproductions
        no_hidden_regression = bool(approvals) and not any(
            review.get("hidden_test_regression") for review in approvals
        )
        permission_reviewed = bool(approvals) and all(
            review.get("permission_reviewed") for review in approvals
        )
        dependency_reviewed = bool(approvals) and all(
            review.get("dependency_reviewed") for review in approvals
        )
        non_regression = bool(approvals) and all(
            float(review.get("fixed_test_delta", -1)) >= 0 for review in approvals
        )
        score = 0.0
        score += 25 if entry.get("signature_verified") else 0
        score += 25 * min(
            len(reproductions) / self.settings.federation_min_reproductions,
            1,
        )
        score += 15 * min(
            len(approvals) / self.settings.federation_registry_min_reviews,
            1,
        )
        score += 10 if non_regression else 0
        score += 10 if no_hidden_regression else 0
        score += 7.5 if permission_reviewed else 0
        score += 7.5 if dependency_reviewed else 0
        hard_block = bool(rejections) or not no_hidden_regression
        allowed = (
            not hard_block
            and bool(entry.get("signature_verified"))
            and enough_reviews
            and enough_reproductions
            and non_regression
            and permission_reviewed
            and dependency_reviewed
            and score >= self.settings.federation_registry_min_trust_score
        )
        return {
            "score": round(score, 2),
            "approvals": len(approvals),
            "rejections": len(rejections),
            "independent_reproductions": len(reproductions),
            "signature_verified": bool(entry.get("signature_verified")),
            "fixed_tests_non_regressing": non_regression,
            "hidden_tests_non_regressing": no_hidden_regression,
            "permission_reviewed": permission_reviewed,
            "dependency_reviewed": dependency_reviewed,
            "admission_allowed": allowed,
        }

    def _candidate_dir(self, pack_id: str) -> Path:
        target = (self.candidates / self._safe_pack_id(pack_id)).resolve()
        target.relative_to(self.candidates.resolve())
        return target

    @staticmethod
    def _safe_pack_id(pack_id: str) -> str:
        if not re.fullmatch(r"evolution-pack_[a-f0-9]{32}", pack_id):
            raise ValueError("Invalid Evolution Pack ID.")
        return pack_id

    @staticmethod
    def _reviews(target: Path) -> list[dict[str, Any]]:
        reviews_dir = target / "reviews"
        if not reviews_dir.is_dir():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(reviews_dir.glob("*.json"))
        ]

    def _ensure_public_files(self) -> None:
        index = self.public / "index.json"
        revocations = self.public / "revocations.json"
        if not index.is_file():
            self._write_json(index, {"schema": "eck-capability-registry.v1", "items": []})
        if not revocations.is_file():
            self._write_json(
                revocations,
                {"schema": "eck-capability-revocations.v1", "items": []},
            )
        self._write_readme()

    def _update_index(self, record: dict[str, Any]) -> None:
        index = self._read_json(self.public / "index.json")
        compact = {
            key: record.get(key)
            for key in (
                "pack_id",
                "pack_type",
                "archive_sha256",
                "status",
                "admitted_at",
                "revoked_at",
                "download_path",
                "trust",
            )
            if record.get(key) is not None
        }
        items = [item for item in index["items"] if item.get("pack_id") != record["pack_id"]]
        items.append(compact)
        index["items"] = sorted(items, key=lambda item: str(item["pack_id"]))
        index["updated_at"] = utc_now().isoformat()
        self._write_json(self.public / "index.json", index)
        self._write_readme()

    def _write_readme(self) -> None:
        index_path = self.public / "index.json"
        if not index_path.is_file():
            return
        index = self._read_json(index_path)
        rows = [
            "# ECK Capability Registry",
            "",
            "Only Cosign-verified packs with independent reproduction and non-regression review ",
            "are admitted. Receiving ECK nodes must still quarantine and retest every pack.",
            "",
            "| Pack | Type | Status | Trust |",
            "| --- | --- | --- | ---: |",
        ]
        rows.extend(
            f"| `{item.get('pack_id')}` | {item.get('pack_type')} | {item.get('status')} | "
            f"{(item.get('trust') or {}).get('score', 0)} |"
            for item in index.get("items", [])
        )
        (self.public / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(path.name)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Invalid Registry JSON object: {path.name}")
        return value

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
