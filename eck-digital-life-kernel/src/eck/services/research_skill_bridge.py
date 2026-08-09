from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import RuntimeSkillRecord, SkillForgeRequest
from eck.events.bus import EventBus
from eck.services.self_model import RepositorySelfModelService
from eck.services.skill_forge import SkillForgeService
from eck.storage.sqlite import SQLiteStore


class ResearchSkillBridgeService:
    _permission_allowlist = {
        "artifact:write",
        "code:execute",
        "network:public",
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        brain: BrainProvider,
        forge: SkillForgeService,
        self_model: RepositorySelfModelService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.brain = brain
        self.forge = forge
        self.self_model = self_model
        self.state_path = settings.research_skill_bridge_state_path

    async def status(self) -> dict[str, Any]:
        research = self._qualified_research_runs()
        generated = [
            item
            for item in self.store.list_runtime_skills(limit=10000)
            if item.source == "eck-generated"
        ]
        active = [item for item in generated if item.status is RuntimeSkillStatus.ACTIVE]
        pending = [
            item
            for item in generated
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
        worker = await self.forge.worker.health()
        if not worker.get("available") or not await self.forge.worker.image_available():
            return await self._record(
                "waiting_worker",
                "The isolated Docker skill worker is not ready; no skill was claimed.",
            )

        generated = [
            item
            for item in self.store.list_runtime_skills(limit=10000)
            if item.source == "eck-generated"
        ]
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
            runtime_skill=skill.model_dump(mode="json"),
        )

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
