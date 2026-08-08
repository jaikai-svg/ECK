from __future__ import annotations

from pathlib import Path
from typing import Any

from eck.config import Settings
from eck.domain.enums import RuntimeSkillStatus
from eck.runtime.worker import DockerSkillWorker
from eck.storage.sqlite import SQLiteStore


class EvolutionAuditService:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        worker: DockerSkillWorker,
    ) -> None:
        self.settings = settings
        self.store = store
        self.worker = worker
        self.project_root = Path(__file__).resolve().parents[3]

    async def status(self) -> dict[str, Any]:
        runtime_skills = self.store.list_runtime_skills(limit=10000)
        generated = [item for item in runtime_skills if item.source == "eck-generated"]
        active_generated = [
            item for item in generated if item.status is RuntimeSkillStatus.ACTIVE
        ]
        failed_generated = [
            item for item in generated if item.status is RuntimeSkillStatus.FAILED
        ]
        worker = await self.worker.health()
        verifier = self.project_root / "scripts" / "verify_release.py"
        return {
            "classification": "partial_self_improvement_not_recursive_agi",
            "current_truth": (
                "ECK can generate, test, automatically repair and hot-activate isolated skills. "
                "It cannot yet autonomously patch or replace the structural kernel, and it does "
                "not train its base-model weights."
            ),
            "verified_now": {
                "skill_self_authoring": True,
                "isolated_worker_available": bool(worker.get("available")),
                "automatic_failed_skill_repair": (
                    self.settings.skill_forge_max_repair_attempts > 0
                ),
                "skill_hot_activation_without_kernel_restart": True,
                "portable_skill_memory": True,
                "release_verifier_present": verifier.is_file(),
                "active_generated_skills": len(active_generated),
                "failed_generated_skills": len(failed_generated),
            },
            "not_yet_verified": {
                "automatic_structural_core_patch": True,
                "shadow_replay_of_core_candidates": True,
                "dual_kernel_zero_downtime_handoff": True,
                "automatic_model_weight_training": True,
                "recursive_open_ended_self_improvement": True,
                "general_agi": True,
            },
            "safety_boundary": {
                "isolated_skill_after_tests": "auto_activate",
                "structural_core_change_after_tests": "human_approval_required",
                "unverified_candidate": "never_activate",
                "rollback": "retain_prior_active_skill_version",
            },
            "next_architecture": [
                {
                    "stage": 1,
                    "name": "Core patch candidate laboratory",
                    "state": "proposed",
                    "result": "Create versioned patch candidates outside the live kernel.",
                },
                {
                    "stage": 2,
                    "name": "Regression and shadow replay gate",
                    "state": "proposed",
                    "result": "Compare fixed benchmarks, failures, safety and resource cost.",
                },
                {
                    "stage": 3,
                    "name": "Human-approved blue-green handoff",
                    "state": "proposed",
                    "result": "Switch versioned workers with health checks and instant rollback.",
                },
            ],
            "research_basis": [
                {
                    "title": "Darwin Gödel Machine",
                    "url": "https://arxiv.org/abs/2505.22954",
                    "adopt": "candidate archive plus empirical benchmark selection",
                },
                {
                    "title": "Voyager",
                    "url": "https://arxiv.org/abs/2305.16291",
                    "adopt": "automatic curriculum plus executable skill library",
                },
                {
                    "title": "Reflexion",
                    "url": "https://arxiv.org/abs/2303.11366",
                    "adopt": "external feedback and episodic repair memory",
                },
                {
                    "title": "SWE-agent",
                    "url": "https://arxiv.org/abs/2405.15793",
                    "adopt": "repository interface and test-driven software repair",
                },
            ],
        }
