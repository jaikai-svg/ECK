from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from eck.core.time import utc_now
from eck.domain.models import RuntimeSkillRecord
from eck.services.federation import FederationService


class ToolCampaignCatalog:
    _allowed_suffixes = {".json", ".md", ".py", ".txt", ".toml", ".yaml", ".yml"}

    def __init__(self, root: Path, federation: FederationService) -> None:
        self.root = root.resolve()
        self.federation = federation
        self.catalog_path = self.root / "catalog.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_catalog()

    async def add(
        self,
        *,
        sequence: int,
        skill: RuntimeSkillRecord,
        source: dict[str, Any],
        gates: dict[str, Any],
        acceptance_examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        catalog = self._read_catalog()
        accepted_count = sum(
            item.get("status", "accepted") == "accepted" for item in catalog["entries"]
        ) + 1
        version = self._toolkit_version(accepted_count)
        archive_result = await self.federation.export_skill(
            skill.runtime_skill_id,
            license_spdx=str(source["license"]),
            source_url=str(source["url"]),
        )
        verification = archive_result.get("verification", {})
        if not verification.get("valid"):
            raise RuntimeError("The generated Evolution Pack failed local verification.")
        slug = self._slug(skill.manifest.name)
        category = self._slug(str(source.get("campaign_category") or "uncategorized"))
        tool_dir = self.root / "tools" / category / slug
        if tool_dir.exists():
            raise FileExistsError(f"Toolkit entry already exists: {tool_dir}")
        self._copy_public_source(Path(skill.source_dir), tool_dir)

        pack_dir = self.root / "evolution-packs" / f"{sequence:03d}-{slug}"
        pack_dir.mkdir(parents=True, exist_ok=False)
        source_archive = self.federation.outbox / str(archive_result["archive"])
        target_archive = pack_dir / f"{slug}-{version}.zip"
        shutil.copy2(source_archive, target_archive)
        archive_sha256 = hashlib.sha256(target_archive.read_bytes()).hexdigest()
        metadata = {
            "schema_version": "eck-toolkit-entry.v1",
            "status": "accepted",
            "sequence": sequence,
            "toolkit_version": version,
            "skill": skill.manifest.model_dump(mode="json"),
            "runtime_skill_id": skill.runtime_skill_id,
            "source": self._public_source(source),
            "gates": gates,
            "acceptance_examples": acceptance_examples,
            "evolution_pack": {
                "pack_id": archive_result["pack_id"],
                "archive": target_archive.relative_to(self.root).as_posix(),
                "sha256": archive_sha256,
                "verification": verification,
            },
            "accepted_at": utc_now().isoformat(),
        }
        (pack_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        catalog["version"] = version
        catalog["accepted_count"] = accepted_count
        catalog["entries"].append(metadata)
        catalog["updated_at"] = utc_now().isoformat()
        self._write_json(self.catalog_path, catalog)
        self._write_readme(catalog)
        return metadata

    def entries(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._read_catalog()["entries"]]

    def next_sequence(self) -> int:
        sequences = [int(item.get("sequence", 0)) for item in self._read_catalog()["entries"]]
        return max(sequences, default=0) + 1

    def revoke(self, runtime_skill_id: str, *, reason: str) -> dict[str, Any]:
        catalog = self._read_catalog()
        selected: dict[str, Any] | None = None
        for item in catalog["entries"]:
            if item.get("runtime_skill_id") != runtime_skill_id:
                continue
            item["status"] = "revoked"
            item["revocation"] = {
                "reason": reason[:2000],
                "revoked_at": utc_now().isoformat(),
            }
            selected = item
            break
        if selected is None:
            raise KeyError(f"Unknown toolkit runtime skill: {runtime_skill_id}")
        accepted_count = sum(
            item.get("status", "accepted") == "accepted" for item in catalog["entries"]
        )
        catalog["accepted_count"] = accepted_count
        catalog["version"] = self._toolkit_version(accepted_count)
        catalog["updated_at"] = utc_now().isoformat()
        self._write_json(self.catalog_path, catalog)
        self._write_readme(catalog)
        return selected

    def status(self) -> dict[str, Any]:
        catalog = self._read_catalog()
        return {
            "repository_name": "eck-agent-toolkit",
            "workspace": str(self.root),
            "version": catalog.get("version"),
            "accepted_count": sum(
                item.get("status", "accepted") == "accepted"
                for item in catalog["entries"]
            ),
            "revoked_count": sum(
                item.get("status") == "revoked" for item in catalog["entries"]
            ),
            "catalog_sha256": hashlib.sha256(
                self.catalog_path.read_bytes()
            ).hexdigest(),
        }

    def _ensure_catalog(self) -> None:
        if self.catalog_path.is_file():
            return
        now = utc_now().isoformat()
        self._write_json(
            self.catalog_path,
            {
                "schema_version": "eck-agent-toolkit.v1",
                "name": "eck-agent-toolkit",
                "version": "0.0.0",
                "accepted_count": 0,
                "entries": [],
                "created_at": now,
                "updated_at": now,
            },
        )
        self._write_readme(self._read_catalog())

    def _read_catalog(self) -> dict[str, Any]:
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
            raise ValueError("The ECK agent toolkit catalog is invalid.")
        return value

    def _write_readme(self, catalog: dict[str, Any]) -> None:
        rows = [
            "# ECK Agent Toolkit",
            "",
            (
                "A versioned collection of ECK-native capability adaptations. Every counted "
                "entry passed license review, static security checks, isolated Docker tests, "
                "fixed acceptance benchmarks, and an additional local reproduction."
            ),
            "",
            f"Current version: `{catalog['version']}`  ",
            f"Verified entries: **{catalog['accepted_count']} / 100**",
            "",
            "| # | Capability | Status | Category | Upstream source | License |",
            "|---:|---|---|---|---|---|",
        ]
        for item in catalog["entries"]:
            source = item["source"]
            skill = item["skill"]
            rows.append(
                f"| {item['sequence']} | `{skill['name']}` | "
                f"{item.get('status', 'accepted')} | {skill['category']} | "
                f"[{source['name']}]({source['url']}) | {source['license']} |"
            )
        rows.extend(
            (
                "",
                "## Verification policy",
                "",
                "- Upstream repositories are read as untrusted reference material.",
                "- Initial licenses are limited to MIT, Apache-2.0, and BSD-family licenses.",
                "- Upstream code is never counted merely because it was downloaded or popular.",
                "- Each Evolution Pack is hash-verified and remains subject to receiver testing.",
                "",
            )
        )
        (self.root / "README.md").write_text("\n".join(rows), encoding="utf-8")

    def _copy_public_source(self, source: Path, target: Path) -> None:
        source = source.resolve()
        if not source.is_dir():
            raise FileNotFoundError(source)
        target.mkdir(parents=True, exist_ok=False)
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError("Toolkit entries cannot contain symbolic links.")
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if path.suffix.casefold() not in self._allowed_suffixes:
                continue
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Toolkit source files must remain below 2 MiB.")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)

    @staticmethod
    def _public_source(source: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "name",
            "url",
            "description",
            "license",
            "commit_sha",
            "stars",
            "updated_at",
            "classification",
            "file_profile",
            "readme_sha256",
            "campaign_category",
        )
        return {key: source.get(key) for key in keys}

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:80]
        if not slug:
            raise ValueError("Toolkit entry name cannot be normalized safely.")
        return slug

    @staticmethod
    def _toolkit_version(count: int) -> str:
        return "1.0.0" if count >= 100 else f"0.{count}.0"

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
