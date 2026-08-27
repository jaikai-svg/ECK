from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from eck.config import Settings
from eck.domain.models import CoreCandidateRequest, EventRecord
from eck.events.bus import EventBus
from eck.services.core_evolution import CoreEvolutionLabService
from eck.services.evolution_transaction import EvolutionTransactionService
from eck.storage.sqlite import SQLiteStore


class AutonomousEvolutionDirectorService:
    """Turns repeated runtime failures into independently evaluated core candidates."""

    _failure_events = {
        "AutonomousProjectFailed",
        "AutonomousProjectQualityRejected",
        "BackgroundWorkerFailed",
        "MissionReactCycleFailed",
        "ProjectPublishFailed",
        "RuntimeSkillRepairFailed",
        "SkillWorkerImageBuildFailed",
        "SleepFailed",
        "SupervisorReviewFailed",
    }
    _progress_states = {
        "candidate_drafting",
        "awaiting_human_approval",
        "approved",
        "activation_applying",
        "restart_pending",
        "rollback_restart_pending",
        "absorbed",
        "rolled_back",
        "startup_mismatch",
    }
    _open_transaction_states = {
        "drafted",
        "awaiting_heldout_evaluation",
        "awaiting_human_approval",
        "approved",
        "activation_applying",
        "restart_pending",
        "rollback_restart_pending",
    }
    _sealed_opportunity_states = {
        "awaiting_human_approval",
        "approved",
        "activation_applying",
        "restart_pending",
        "rollback_restart_pending",
        "absorbed",
        "rolled_back",
        "startup_mismatch",
    }
    _worker_targets = {
        "autonomous_curriculum": (
            "src/eck/services/autonomous_learning.py",
            "tests/unit/test_autonomous_learning.py",
        ),
        "autonomous_project_lab": (
            "src/eck/services/project_lab.py",
            "tests/unit/test_p5_self_development.py",
        ),
        "durable_mission_executor": (
            "src/eck/experimental/p6/mission_executor.py",
            "tests/unit/test_p6_mission_executor.py",
        ),
        "research_skill_bridge": (
            "src/eck/services/research_skill_bridge.py",
            "tests/unit/test_guided_skill_acquisition.py",
        ),
        "supervisor": (
            "src/eck/services/supervisor.py",
            "tests/unit/test_supervisor.py",
        ),
        "tool_acquisition_campaign": (
            "src/eck/services/tool_campaign.py",
            "tests/unit/test_tool_campaign.py",
        ),
    }
    _event_targets = {
        "AutonomousProjectFailed": (
            "src/eck/services/project_lab_components/lifecycle.py",
            "tests/unit/test_p5_self_development.py",
        ),
        "AutonomousProjectQualityRejected": (
            "src/eck/services/project_lab_components/validation.py",
            "tests/unit/test_p5_self_development.py",
        ),
        "MissionReactCycleFailed": (
            "src/eck/experimental/p6/mission_executor.py",
            "tests/unit/test_p6_mission_executor.py",
        ),
        "ProjectPublishFailed": (
            "src/eck/services/project_lab_components/github.py",
            "tests/unit/test_p5_self_development.py",
        ),
        "RuntimeSkillRepairFailed": (
            "src/eck/services/skill_forge.py",
            "tests/unit/test_guided_skill_acquisition.py",
        ),
        "SkillWorkerImageBuildFailed": (
            "src/eck/services/skill_forge.py",
            "tests/unit/test_guided_skill_acquisition.py",
        ),
        "SleepFailed": (
            "src/eck/kernel/runtime.py",
            "tests/unit/test_kernel_resources.py",
        ),
        "SupervisorReviewFailed": (
            "src/eck/services/supervisor.py",
            "tests/unit/test_supervisor.py",
        ),
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        core_lab: CoreEvolutionLabService,
        transactions: EvolutionTransactionService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.core_lab = core_lab
        self.transactions = transactions
        self.project_root = Path(__file__).resolve().parents[3]

    def status(self) -> dict[str, Any]:
        items = self.store.list_evolution_opportunities(limit=500)
        counts: dict[str, int] = {}
        for item in items:
            state = str(item["status"])
            counts[state] = counts.get(state, 0) + 1
        return {
            "schema_version": "eck-autonomous-evolution-director.v1",
            "enabled": self.settings.autonomous_evolution_director_enabled,
            "candidate_drafting_enabled": (
                self.settings.autonomous_core_candidate_drafting_enabled
            ),
            "failure_threshold": self.settings.autonomous_evolution_failure_threshold,
            "event_window": self.settings.autonomous_evolution_event_window,
            "opportunity_count": len(items),
            "status_counts": counts,
            "ready_count": counts.get("ready", 0),
            "awaiting_human_approval": counts.get("awaiting_human_approval", 0),
            "activation_policy": "independent_heldout_then_human_approval",
            "latest": items[0] if items else None,
        }

    def list_opportunities(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_evolution_opportunities(limit=limit, status=status)

    def get(self, opportunity_id: str) -> dict[str, Any]:
        return self.store.get_evolution_opportunity(opportunity_id)

    async def scan(self) -> dict[str, Any]:
        created = 0
        updated = 0
        for event in self.store.list_recent_events(
            limit=self.settings.autonomous_evolution_event_window
        ):
            if event.event_type not in self._failure_events:
                continue
            signature, evidence = self._failure_signature(event)
            existing = self.store.get_evolution_opportunity_by_signature(signature)
            if existing is None:
                opportunity = self._new_opportunity(event, signature, evidence)
                created += 1
                await self.events.publish(
                    "EvolutionOpportunityDiscovered",
                    str(opportunity["opportunity_id"]),
                    {
                        "event_type": event.event_type,
                        "worker": opportunity["worker"],
                        "occurrence_count": opportunity["occurrence_count"],
                        "status": opportunity["status"],
                    },
                )
                continue
            if event.event_id in existing["evidence_event_ids"]:
                continue
            sequences = sorted(
                {*(int(value) for value in existing["evidence_sequences"]), event.sequence}
            )
            event_ids = [*existing["evidence_event_ids"], event.event_id]
            count = len(event_ids)
            status, readiness = self._readiness(
                count=count,
                target_files=list(existing["target_files"]),
                heldout_pack_id=existing.get("heldout_pack_id"),
                current_status=str(existing["status"]),
            )
            self.store.update_evolution_opportunity(
                str(existing["opportunity_id"]),
                occurrence_count=count,
                evidence_sequences=sequences,
                evidence_event_ids=event_ids,
                last_seen_at=event.created_at.isoformat(),
                status=status,
                readiness=readiness,
            )
            updated += 1
        reconciled = self._reconcile_transactions()
        return {
            **self.status(),
            "scan": {"created": created, "updated": updated, "reconciled": reconciled},
        }

    async def attach_pack(self, opportunity_id: str, pack_id: str) -> dict[str, Any]:
        opportunity = self.get(opportunity_id)
        if str(opportunity["status"]) in self._sealed_opportunity_states:
            raise RuntimeError(
                "A sealed or human-reviewed evolution opportunity cannot change its pack."
            )
        pack = self.transactions.get_heldout_pack(pack_id)
        status, readiness = self._readiness(
            count=int(opportunity["occurrence_count"]),
            target_files=list(opportunity["target_files"]),
            heldout_pack_id=str(pack["pack_id"]),
            current_status=str(opportunity["status"]),
            allow_reopen=True,
        )
        updated = self.store.update_evolution_opportunity(
            opportunity_id,
            heldout_pack_id=str(pack["pack_id"]),
            status=status,
            readiness=readiness,
            error="",
        )
        await self.events.publish(
            "EvolutionOpportunityHeldoutAttached",
            opportunity_id,
            {"pack_id": pack["pack_id"], "pack_sha256": pack["pack_sha256"]},
        )
        return updated

    async def run_if_needed(self, *, force: bool = False) -> dict[str, Any]:
        scan = await self.scan()
        if not self.settings.autonomous_evolution_director_enabled and not force:
            return {**scan, "run": "disabled"}
        if not self.settings.autonomous_core_candidate_drafting_enabled:
            return {**scan, "run": "candidate_drafting_disabled"}
        active = [
            item
            for item in self.store.list_evolution_transactions(limit=500)
            if str(item["status"]) in self._open_transaction_states
        ]
        if active:
            return {
                **scan,
                "run": "waiting_existing_evolution_transaction",
                "transaction_id": active[0]["transaction_id"],
            }
        ready = self.list_opportunities(limit=1, status="ready")
        if not ready:
            return {**scan, "run": "no_independently_evaluable_opportunity"}
        result = await self.run_opportunity(str(ready[0]["opportunity_id"]))
        return {**scan, "run": "evaluated", "opportunity": result}

    async def run_opportunity(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = self.get(opportunity_id)
        if not self.settings.autonomous_core_candidate_drafting_enabled:
            raise RuntimeError("Autonomous core candidate drafting is disabled.")
        if opportunity["status"] != "ready":
            raise RuntimeError(
                f"Evolution opportunity is not ready: {opportunity['status']}"
            )
        pack_id = str(opportunity.get("heldout_pack_id") or "")
        if not pack_id:
            raise RuntimeError("An independent held-out pack is required before drafting.")
        self.transactions.get_heldout_pack(pack_id)
        self.store.update_evolution_opportunity(
            opportunity_id,
            status="candidate_drafting",
            error="",
        )
        try:
            candidate_id = str(opportunity.get("candidate_id") or "")
            if candidate_id:
                candidate = self.core_lab.get_candidate(candidate_id)
                if not bool(candidate.get("validation", {}).get("passed")):
                    candidate_id = ""
            if not candidate_id:
                candidate = await self.core_lab.create_candidate(
                    CoreCandidateRequest(
                        objective=str(opportunity["objective"]),
                        target_files=tuple(opportunity["target_files"][:3]),
                        allow_new_files=False,
                    )
                )
                candidate_id = str(candidate["candidate_id"])
                self.store.update_evolution_opportunity(
                    opportunity_id,
                    candidate_id=candidate_id,
                )
            if not bool(candidate.get("validation", {}).get("passed")):
                return self.store.update_evolution_opportunity(
                    opportunity_id,
                    status="fixed_gates_failed",
                    error="The isolated candidate failed fixed validation gates.",
                )
            transaction = await self.transactions.evaluate(candidate_id, pack_id)
            state = str(transaction["status"])
            updated = self.store.update_evolution_opportunity(
                opportunity_id,
                status=state,
                error=str(transaction.get("error") or ""),
            )
            await self.events.publish(
                "EvolutionOpportunityCandidateEvaluated",
                opportunity_id,
                {
                    "candidate_id": candidate_id,
                    "transaction_id": transaction["transaction_id"],
                    "status": state,
                },
                correlation_id=candidate_id,
            )
            return updated
        except Exception as exc:
            self.store.update_evolution_opportunity(
                opportunity_id,
                status="evaluation_failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def _new_opportunity(
        self,
        event: EventRecord,
        signature: str,
        evidence: dict[str, str],
    ) -> dict[str, Any]:
        target = self._target_for(event.event_type, evidence["worker"])
        target_files = [target[0]] if target else []
        test_files = [target[1]] if target else []
        status, readiness = self._readiness(
            count=1,
            target_files=target_files,
            heldout_pack_id=None,
            current_status="observing",
        )
        failure = evidence["failure_type"] or "unknown failure"
        worker = evidence["worker"] or event.event_type
        return self.store.create_evolution_opportunity(
            {
                "signature_sha256": signature,
                "status": status,
                "title": f"Repeated {worker} failure: {failure}"[:300],
                "objective": (
                    f"Correct the repeatedly observed {event.event_type} failure in {worker}. "
                    "Preserve all backward-compatible APIs and pass fixed regression plus the "
                    "independently registered held-out evaluation before requesting approval."
                ),
                "event_type": event.event_type,
                "worker": evidence["worker"],
                "failure_type": evidence["failure_type"],
                "occurrence_count": 1,
                "evidence_sequences": [event.sequence],
                "evidence_event_ids": [event.event_id],
                "target_files": target_files,
                "test_files": test_files,
                "readiness": readiness,
                "first_seen_at": event.created_at.isoformat(),
                "last_seen_at": event.created_at.isoformat(),
            }
        )

    def _readiness(
        self,
        *,
        count: int,
        target_files: list[str],
        heldout_pack_id: str | None,
        current_status: str,
        allow_reopen: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        repeated = count >= self.settings.autonomous_evolution_failure_threshold
        target_known = bool(target_files)
        heldout_bound = bool(heldout_pack_id)
        ready = repeated and target_known and heldout_bound
        readiness = {
            "failure_threshold": self.settings.autonomous_evolution_failure_threshold,
            "occurrence_count": count,
            "repeated_failure": repeated,
            "target_known": target_known,
            "heldout_pack_bound": heldout_bound,
            "independent_evaluation_required": True,
            "ready_to_draft": ready,
        }
        if current_status in self._progress_states and not allow_reopen:
            return current_status, readiness
        if ready:
            return "ready", readiness
        if not repeated:
            return "observing", readiness
        if not target_known:
            return "target_unknown", readiness
        return "waiting_heldout_pack", readiness

    def _reconcile_transactions(self) -> int:
        reconciled = 0
        for opportunity in self.list_opportunities(limit=500):
            candidate_id = str(opportunity.get("candidate_id") or "")
            if not candidate_id:
                continue
            try:
                transaction = self.transactions.get_for_candidate(candidate_id)
            except KeyError:
                continue
            status = str(transaction["status"])
            if status == opportunity["status"]:
                continue
            self.store.update_evolution_opportunity(
                str(opportunity["opportunity_id"]),
                status=status,
                error=str(transaction.get("error") or ""),
            )
            reconciled += 1
        return reconciled

    def _failure_signature(self, event: EventRecord) -> tuple[str, dict[str, str]]:
        payload = event.payload
        worker = str(payload.get("worker") or self._event_worker(event.event_type))
        raw_error = str(payload.get("error") or payload.get("detail") or "")
        failure_type = str(
            payload.get("type")
            or payload.get("step_status")
            or (raw_error.split(":", 1)[0] if raw_error else "")
        )
        details = payload.get("detail")
        if details is None:
            details = payload.get("error")
        if details is None:
            details = payload.get("correction")
        if details is None:
            details = payload.get("issues")
        if details is None:
            details = payload.get("observation")
        normalized = self._normalize(details)
        material = "|".join((event.event_type, worker, failure_type, normalized))
        return hashlib.sha256(material.encode("utf-8")).hexdigest(), {
            "worker": worker,
            "failure_type": failure_type,
        }

    @staticmethod
    def _normalize(value: Any) -> str:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        serialized = re.sub(r"[A-Fa-f0-9]{16,}", "<id>", serialized)
        serialized = re.sub(r"[A-Za-z]:[/\\][^\"']+", "<path>", serialized)
        serialized = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", serialized)
        return re.sub(r"\s+", " ", serialized).strip()[:1000]

    @staticmethod
    def _event_worker(event_type: str) -> str:
        return {
            "AutonomousProjectFailed": "autonomous_project_lab",
            "AutonomousProjectQualityRejected": "autonomous_project_lab",
            "MissionReactCycleFailed": "durable_mission_executor",
            "ProjectPublishFailed": "autonomous_project_lab",
            "RuntimeSkillRepairFailed": "skill_forge",
            "SkillWorkerImageBuildFailed": "skill_forge",
            "SleepFailed": "sleep",
            "SupervisorReviewFailed": "supervisor",
        }.get(event_type, "")

    def _target_for(self, event_type: str, worker: str) -> tuple[str, str] | None:
        target = self._event_targets.get(event_type) or self._worker_targets.get(worker)
        if target is None:
            return None
        if not all((self.project_root / relative).is_file() for relative in target):
            return None
        return target
