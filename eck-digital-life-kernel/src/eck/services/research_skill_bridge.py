from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import (
    GuidedSkillAcquisitionRequest,
    RuntimeSkillRecord,
    SkillForgeRequest,
)
from eck.events.bus import EventBus
from eck.services.community_sources import CommunitySourceCatalog
from eck.services.self_model import RepositorySelfModelService
from eck.services.skill_forge import SkillForgeService
from eck.storage.sqlite import SQLiteStore


class ResearchSkillBridgeService:
    _permission_allowlist = {
        "artifact:write",
        "code:execute",
        "network:public",
    }
    _permissive_licenses = {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MIT",
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        brain: BrainProvider,
        forge: SkillForgeService,
        self_model: RepositorySelfModelService,
        community_sources: CommunitySourceCatalog,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.brain = brain
        self.forge = forge
        self.self_model = self_model
        self.community_sources = community_sources
        self.state_path = settings.research_skill_bridge_state_path

    async def status(self) -> dict[str, Any]:
        research = self._qualified_research_runs()
        generated = [
            item
            for item in self.store.list_runtime_skills(limit=10000)
            if item.source == "eck-generated"
        ]
        latest = self._latest_by_name(generated)
        active = [item for item in latest if item.status is RuntimeSkillStatus.ACTIVE]
        pending = [
            item
            for item in latest
            if item.status
            in {
                RuntimeSkillStatus.DRAFT,
                RuntimeSkillStatus.FAILED,
                RuntimeSkillStatus.TESTING,
            }
        ]
        state = self._read_state()
        worker = await self.forge.worker.health()
        worker_image = await self.forge.worker.image_status()
        return {
            "enabled": self.settings.research_skill_bridge_enabled,
            "state": state,
            "qualified_research_runs": len(research),
            "minimum_research_runs": self.settings.research_skill_bridge_min_research_runs,
            "generated_skill_candidates": len(generated),
            "active_generated_skills": len(active),
            "pending_generated_skills": len(pending),
            "failed_attempt_history": sum(
                item.status is RuntimeSkillStatus.FAILED for item in generated
            ),
            "worker": worker,
            "worker_image": worker_image,
            "conversion_verified": bool(active),
            "claim_policy": (
                "Research is not executable learning. Conversion is counted only after an "
                "ECK-generated skill passes its isolated worker tests and becomes active."
            ),
        }

    async def run_if_needed(self, *, force: bool = False) -> dict[str, Any]:
        if not self.settings.research_skill_bridge_enabled:
            return await self._record("disabled", "Research-to-skill conversion is disabled.")
        if not force and not self._cooldown_elapsed():
            return {"status": "cooldown", "state": self._read_state()}
        worker_image = await self.forge.ensure_worker_image()
        if not worker_image.get("success"):
            return await self._record(
                "waiting_worker",
                "The isolated Docker skill worker or its image is not ready.",
                detail=str(worker_image.get("detail", ""))[-2000:],
            )

        generated_history = [
            item
            for item in self.store.list_runtime_skills(limit=10000)
            if item.source == "eck-generated"
        ]
        generated = self._latest_by_name(generated_history)
        pending = next(
            (
                item
                for item in generated
                if item.status in {RuntimeSkillStatus.DRAFT, RuntimeSkillStatus.TESTING}
                or (
                    item.status is RuntimeSkillStatus.FAILED
                    and bool(item.test_report.get("worker_unavailable"))
                )
            ),
            None,
        )
        if pending is not None:
            result = await self.forge.validate_skill(pending.runtime_skill_id)
            return await self._record(
                "revalidated_existing_candidate",
                "An existing generated skill candidate was re-tested before creating another.",
                runtime_skill=result,
            )

        repairable = next(
            (
                item
                for item in generated
                if item.status is RuntimeSkillStatus.FAILED
                and self._repair_attempts(item) < self.settings.skill_forge_max_repair_attempts
            ),
            None,
        )
        if repairable is not None:
            try:
                repaired = await self.forge.repair_failed_skill(repairable.runtime_skill_id)
            except httpx.HTTPError as exc:
                return await self._record(
                    "repair_provider_unavailable",
                    f"The local brain did not complete the bounded repair: {type(exc).__name__}",
                    runtime_skill_id=repairable.runtime_skill_id,
                )
            except ValueError as exc:
                return await self._record(
                    "repair_candidate_invalid",
                    str(exc)[:1000],
                    runtime_skill_id=repairable.runtime_skill_id,
                )
            return await self._record(
                "skill_repaired_and_activated"
                if repaired.status is RuntimeSkillStatus.ACTIVE
                else "skill_repair_not_activated",
                (
                    "A failed generated skill was repaired, re-tested, and activated."
                    if repaired.status is RuntimeSkillStatus.ACTIVE
                    else "A bounded repair candidate was tested but did not pass activation."
                ),
                runtime_skill=repaired.model_dump(mode="json"),
            )

        research = self._qualified_research_runs()
        if len(research) < self.settings.research_skill_bridge_min_research_runs:
            return await self._record(
                "insufficient_research_evidence",
                "Not enough completed, conclusive, multi-source research runs are available.",
                qualified_research_runs=len(research),
            )
        research_fingerprint = self._research_fingerprint(research[:12])
        if not force and self._read_state().get("research_fingerprint") == research_fingerprint:
            return {
                "status": "no_new_research",
                "state": self._read_state(),
                "message": (
                    "No new qualified evidence exists since the previous conversion attempt."
                ),
            }
        try:
            request, evidence_run_ids, reason = await self._propose(research[:12])
        except httpx.HTTPError as exc:
            return await self._record(
                "proposal_provider_unavailable",
                f"The local brain did not complete skill-gap selection: {type(exc).__name__}",
            )
        if request is None:
            return await self._record(
                "no_valid_skill_gap",
                reason or "The model did not identify a bounded executable skill gap.",
                evidence_run_ids=evidence_run_ids,
                research_fingerprint=research_fingerprint,
            )
        try:
            skill = await self.forge.forge(request)
        except httpx.HTTPError as exc:
            return await self._record(
                "forge_provider_unavailable",
                f"The local brain did not complete skill generation: {type(exc).__name__}",
                evidence_run_ids=evidence_run_ids,
            )
        return await self._record(
            "skill_activated"
            if skill.status is RuntimeSkillStatus.ACTIVE
            else "skill_candidate_not_activated",
            (
                "The generated skill passed isolated validation and became active."
                if skill.status is RuntimeSkillStatus.ACTIVE
                else "A real candidate was created, but it did not pass activation gates."
            ),
            evidence_run_ids=evidence_run_ids,
            research_fingerprint=research_fingerprint,
            runtime_skill=skill.model_dump(mode="json"),
        )

    async def acquire(self, request: GuidedSkillAcquisitionRequest) -> dict[str, Any]:
        worker = await self.forge.ensure_worker_image()
        if not worker.get("success"):
            return await self._record(
                "guided_waiting_worker",
                "The requested skill was not forged because the isolated worker is unavailable.",
                detail=str(worker.get("detail", ""))[-2000:],
            )
        source_context = await self._guided_sources(request)
        return await self.acquire_inspected(request, source_context, worker_checked=True)

    async def inspect_github_repository(self, value: str) -> dict[str, Any]:
        """Inspect and pin one public GitHub source without executing upstream code."""
        return await self._inspect_github_repository(value)

    async def acquire_inspected(
        self,
        request: GuidedSkillAcquisitionRequest,
        source_context: list[dict[str, Any]],
        *,
        worker_checked: bool = False,
    ) -> dict[str, Any]:
        if not worker_checked:
            worker = await self.forge.ensure_worker_image()
            if not worker.get("success"):
                return await self._record(
                    "guided_waiting_worker",
                    "The requested skill was not forged because the isolated worker "
                    "is unavailable.",
                    detail=str(worker.get("detail", ""))[-2000:],
                )
        expected_urls = set(request.source_urls)
        actual_urls = {str(item.get("url", "")) for item in source_context}
        if expected_urls and expected_urls != actual_urls:
            raise ValueError("Inspected source context does not match the requested source URLs.")
        public_sources = self._public_source_records(source_context)
        blocked = [item for item in source_context if not item.get("adaptation_allowed")]
        if blocked:
            return await self._record(
                "guided_license_blocked",
                "Source analysis completed, but adaptation stopped because its license is not "
                "in ECK's automatic permissive-license allowlist.",
                sources=public_sources,
            )
        forge_request, reason = await self._propose_guided(request, source_context)
        skill = await self.forge.forge(forge_request)
        provenance = {
            "schema_version": "eck-skill-provenance.v1",
            "requested_topic": request.topic,
            "requested_objective": request.objective,
            "decision_reason": reason,
            "sources": public_sources,
            "policy": (
                "Upstream content was treated as untrusted research material. ECK generated an "
                "ECK-native implementation and did not execute upstream code."
            ),
        }
        provenance_path = Path(skill.source_dir) / "provenance.json"
        provenance_path.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        status = (
            "guided_skill_activated"
            if skill.status is RuntimeSkillStatus.ACTIVE
            else "guided_skill_not_activated"
        )
        return await self._record(
            status,
            (
                "The requested skill passed isolated tests and Canary replay."
                if skill.status is RuntimeSkillStatus.ACTIVE
                else "A real candidate was generated, but it failed isolated activation gates."
            ),
            sources=public_sources,
            runtime_skill=skill.model_dump(mode="json"),
        )

    @staticmethod
    def _public_source_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in item.items() if key != "readme_excerpt"}
            for item in items
        ]

    async def _propose_guided(
        self,
        request: GuidedSkillAcquisitionRequest,
        sources: list[dict[str, Any]],
    ) -> tuple[SkillForgeRequest, str]:
        active_names = {
            item.manifest.name
            for item in self.store.list_runtime_skills(limit=10000)
            if item.status is RuntimeSkillStatus.ACTIVE
        }
        name = request.requested_name or self._guided_name(request.topic)
        if name in active_names:
            raise ValueError(f"The requested skill already exists and is active: {name}")
        source_lines = [
            f"{item.get('url')}@{item.get('commit_sha') or 'catalog'} ({item.get('license')})"
            for item in sources
        ]
        objective = request.objective
        if source_lines:
            objective = f"{objective}\nVerified upstream provenance: {'; '.join(source_lines)}"
        category = str(sources[0].get("classification", "guided-acquisition")) if sources else (
            "guided-acquisition"
        )
        forge_request = SkillForgeRequest(
            name=name,
            objective=objective[:3000],
            category=category[:80],
            operations=("execute",),
            permissions=self._guided_permissions(request.objective),
            dependencies=(),
            acceptance_examples=request.acceptance_examples,
        )
        return (
            forge_request,
            "The operator supplied the capability contract; ECK deterministically compiled the "
            "manifest and reserved model inference for implementation and tests.",
        )

    @staticmethod
    def _guided_name(topic: str) -> str:
        terms = re.findall(r"[a-z][a-z0-9]{2,}", topic.casefold())[:4]
        suffix = "_".join(terms) if terms else hashlib.sha256(
            topic.encode("utf-8")
        ).hexdigest()[:12]
        return f"guided.{suffix}"[:80]

    @staticmethod
    def _guided_permissions(objective: str) -> tuple[str, ...]:
        value = objective.casefold()
        permissions = []
        if re.search(r"\b(fetch|http request|public web|download url)\b|公開網頁|下載網址", value):
            permissions.append("network:public")
        if re.search(r"\b(write artifact|create file|export file)\b|寫入成品|建立檔案", value):
            permissions.append("artifact:write")
        if re.search(r"\b(run code|execute code|compile project)\b|執行程式|編譯專案", value):
            permissions.append("code:execute")
        return tuple(permissions)

    async def _guided_sources(
        self,
        request: GuidedSkillAcquisitionRequest,
    ) -> list[dict[str, Any]]:
        if request.source_urls:
            if not self.settings.network_enabled:
                raise ValueError(
                    "Public network research is disabled; the supplied source was not read."
                )
            return [await self._inspect_github_repository(url) for url in request.source_urls]
        matched = self.community_sources.match(f"{request.topic} {request.objective}")
        if matched is None or matched["license"] not in self._permissive_licenses:
            return []
        return [
            {
                **matched,
                "commit_sha": None,
                "classification": "catalog-pattern-candidate",
                "adaptation_allowed": matched["license"] in self._permissive_licenses,
                "inspection": "Catalog metadata only; no upstream code was executed.",
            }
        ]

    async def _inspect_github_repository(self, value: str) -> dict[str, Any]:
        parsed = urlsplit(value.strip())
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or len(parts) < 2
        ):
            raise ValueError(
                "Guided source URLs must be public https://github.com/owner/repo URLs."
            )
        owner, repository = parts[:2]
        repository = repository.removesuffix(".git")
        api_root = f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ECK-Digital-Life-Kernel/0.1 capability-miner",
        }
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=False,
            headers=headers,
        ) as client:
            repository_response = await client.get(api_root)
            repository_response.raise_for_status()
            metadata = repository_response.json()
            if not isinstance(metadata, dict) or metadata.get("private"):
                raise ValueError("Only public GitHub repositories can enter guided acquisition.")
            branch = str(metadata.get("default_branch") or "main")
            commit_response = await client.get(f"{api_root}/commits/{quote(branch, safe='')}")
            commit_response.raise_for_status()
            commit = commit_response.json()
            tree_response = await client.get(
                f"{api_root}/git/trees/{quote(branch, safe='')}",
                params={"recursive": "1"},
            )
            tree_response.raise_for_status()
            tree = tree_response.json()
            readme_response = await client.get(f"{api_root}/readme")
            readme_response.raise_for_status()
            readme = readme_response.json()
        license_spdx = str((metadata.get("license") or {}).get("spdx_id") or "UNKNOWN")
        paths = [
            str(item.get("path", ""))
            for item in tree.get("tree", [])
            if isinstance(item, dict) and item.get("type") == "blob"
        ][:5000]
        markdown_count = sum(Path(path).suffix.casefold() in {".md", ".mdx"} for path in paths)
        code_count = sum(
            Path(path).suffix.casefold()
            in {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs"}
            for path in paths
        )
        classification = (
            "strategy-and-role-library"
            if markdown_count > max(code_count * 2, 10)
            else "executable-pattern-library"
        )
        readme_excerpt = self._decode_github_content(readme)[:12000]
        return {
            "name": str(metadata.get("full_name") or f"{owner}/{repository}"),
            "url": str(metadata.get("html_url") or value),
            "description": str(metadata.get("description") or "")[:1000],
            "license": license_spdx,
            "commit_sha": str(commit.get("sha") or "")[:40],
            "default_branch": branch,
            "stars": int(metadata.get("stargazers_count") or 0),
            "updated_at": str(metadata.get("updated_at") or ""),
            "classification": classification,
            "file_profile": {"markdown": markdown_count, "code": code_count},
            "readme_sha256": hashlib.sha256(readme_excerpt.encode("utf-8")).hexdigest(),
            "readme_excerpt": readme_excerpt,
            "adaptation_allowed": license_spdx in self._permissive_licenses,
            "inspection": (
                "Pinned metadata, tree, and README were inspected; upstream code was not run."
            ),
            "archived": bool(metadata.get("archived")),
            "fork": bool(metadata.get("fork")),
        }

    @staticmethod
    def _decode_github_content(payload: Any) -> str:
        if not isinstance(payload, dict) or payload.get("encoding") != "base64":
            return ""
        try:
            return base64.b64decode(str(payload.get("content", ""))).decode(
                "utf-8",
                errors="replace",
            )
        except (ValueError, TypeError):
            return ""

    async def _propose(
        self,
        research: list[dict[str, Any]],
    ) -> tuple[SkillForgeRequest | None, list[str], str]:
        active_names = {
            item.manifest.name
            for item in self.store.list_runtime_skills(limit=10000)
            if item.status is RuntimeSkillStatus.ACTIVE
        }
        summaries = [
            {
                "run_id": item["run_id"],
                "topic": item["topic"],
                "conclusion": item["conclusion"],
                "confidence": item["confidence"],
                "claims": item["claims"][:5],
                "sources": [source["canonical_url"] for source in item["sources"][:6]],
            }
            for item in research
        ]
        self_summary = self.self_model.ensure().get("summary", {})
        response = await self.brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are ECK's research-to-skill conversion verifier. Select at most one "
                        "small, reusable, executable Python skill that directly closes a "
                        "capability gap supported by the supplied research. Do not propose "
                        "knowledge summaries, core patches, social actions, credential use, "
                        "paid APIs, or a duplicate active skill. Return decision=no_action "
                        "when evidence is insufficient. Dependencies must be free PyPI package "
                        "specifiers. Use only the supplied permission values."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "active_skills": sorted(active_names),
                            "repository_summary": self_summary,
                            "allowed_permissions": sorted(self._permission_allowlist),
                            "research": summaries,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            format_schema={
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": ["forge", "no_action"]},
                    "reason": {"type": "string"},
                    "name": {"type": "string"},
                    "objective": {"type": "string"},
                    "category": {"type": "string"},
                    "operations": {"type": "array", "items": {"type": "string"}},
                    "permissions": {"type": "array", "items": {"type": "string"}},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "evidence_run_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["decision", "reason", "evidence_run_ids"],
            },
            options={"num_predict": 768, "think": False},
        )
        payload = self._json_object(response.content)
        reason = str(payload.get("reason", ""))[:2000]
        valid_run_ids = {str(item["run_id"]) for item in research}
        evidence_run_ids = [
            str(item)
            for item in payload.get("evidence_run_ids", [])
            if str(item) in valid_run_ids
        ]
        if payload.get("decision") != "forge" or not evidence_run_ids:
            return None, evidence_run_ids, reason
        name = str(payload.get("name", "")).strip()
        if name in active_names:
            return None, evidence_run_ids, "The proposed skill duplicates an active skill."
        permissions = tuple(
            str(item)
            for item in payload.get("permissions", [])
            if str(item) in self._permission_allowlist
        )
        dependencies = tuple(str(item).strip()[:200] for item in payload.get("dependencies", []))
        objective = str(payload.get("objective", "")).strip()
        objective = f"{objective}\nVerified research basis: {', '.join(evidence_run_ids)}"
        try:
            request = SkillForgeRequest(
                name=name,
                objective=objective,
                category=str(payload.get("category", "research-derived"))[:80],
                operations=tuple(str(item) for item in payload.get("operations", [])),
                permissions=permissions,
                dependencies=dependencies[:8],
            )
        except ValueError as exc:
            return None, evidence_run_ids, f"Invalid generated skill specification: {exc}"
        return request, evidence_run_ids, reason

    def _qualified_research_runs(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.store.list_research_runs(limit=100)
            if item.get("status") == "completed"
            and item.get("conclusion_status") not in {None, "", "inconclusive"}
            and len(item.get("sources", [])) >= 2
            and float(item.get("confidence") or 0) >= 0.5
        ]

    @staticmethod
    def _latest_by_name(items: list[RuntimeSkillRecord]) -> list[RuntimeSkillRecord]:
        latest: dict[str, RuntimeSkillRecord] = {}
        for item in items:
            latest.setdefault(item.manifest.name, item)
        return list(latest.values())

    @staticmethod
    def _research_fingerprint(items: list[dict[str, Any]]) -> str:
        run_ids = sorted(str(item.get("run_id", "")) for item in items)
        return hashlib.sha256("\n".join(run_ids).encode("utf-8")).hexdigest()

    def _cooldown_elapsed(self) -> bool:
        state = self._read_state()
        last_attempt = state.get("attempted_at")
        if not last_attempt:
            return True
        try:
            elapsed = (utc_now() - datetime.fromisoformat(str(last_attempt))).total_seconds()
        except ValueError:
            return True
        return elapsed >= self.settings.research_skill_bridge_interval_seconds

    def _repair_attempts(self, failed: RuntimeSkillRecord) -> int:
        versions = sorted(
            [
                item
                for item in self.store.list_runtime_skills(limit=10000)
                if item.source == "eck-generated"
                and item.manifest.name == failed.manifest.name
            ],
            key=lambda item: tuple(
                int(part) for part in item.manifest.version.split(".")
            ),
        )
        return sum(
            not self._same_skill_source(previous, current)
            for previous, current in zip(versions, versions[1:], strict=False)
        )

    @staticmethod
    def _same_skill_source(
        previous: RuntimeSkillRecord,
        current: RuntimeSkillRecord,
    ) -> bool:
        previous_dir = Path(previous.source_dir)
        current_dir = Path(current.source_dir)
        pairs = (
            (
                previous_dir / previous.manifest.entrypoint,
                current_dir / current.manifest.entrypoint,
            ),
            (previous_dir / "test_skill.py", current_dir / "test_skill.py"),
        )
        try:
            return all(left.read_bytes() == right.read_bytes() for left, right in pairs)
        except OSError:
            return False

    async def _record(self, status: str, message: str, **details: Any) -> dict[str, Any]:
        state = {
            "schema_version": "eck-research-skill-bridge.v1",
            "attempted_at": utc_now().isoformat(),
            "status": status,
            "message": message,
            **details,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)
        await self.events.publish(
            "ResearchSkillBridgeEvaluated",
            self.settings.identity,
            {
                "status": status,
                "message": message,
                "runtime_skill_id": self._runtime_skill_id(details.get("runtime_skill")),
            },
        )
        return state

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schema_version": "eck-research-skill-bridge.v1",
                "status": "never_run",
            }
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "invalid_state"}

    @staticmethod
    def _runtime_skill_id(value: Any) -> str | None:
        if isinstance(value, RuntimeSkillRecord):
            return value.runtime_skill_id
        if isinstance(value, dict):
            result = value.get("runtime_skill_id")
            return str(result) if result else None
        return None

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                return {}
            value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, dict) else {}
