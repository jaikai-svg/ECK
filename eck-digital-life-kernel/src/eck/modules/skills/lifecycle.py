from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import RuntimeSkillRecord, SkillRecord


class SkillSourceKind(StrEnum):
    EXPERIENCE = "experience"
    RUNTIME = "runtime"


class SkillLifecyclePhase(StrEnum):
    CANDIDATE = "candidate"
    TESTING = "testing"
    VERIFIED = "verified"
    ACTIVE = "active"
    FAILED = "failed"
    RETIRED = "retired"


class SkillLifecycleItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_id: str
    name: str
    capability: str
    source_kind: SkillSourceKind
    phase: SkillLifecyclePhase
    executable: bool
    verified: bool
    active: bool
    version: str | None = None
    source: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SkillLifecycleRepository(Protocol):
    def list_skills(self, limit: int = 100) -> list[SkillRecord]: ...

    def list_runtime_skills(self, limit: int = 200) -> list[RuntimeSkillRecord]: ...


class RuntimeSkillTransitionPolicy:
    _allowed: dict[RuntimeSkillStatus, frozenset[RuntimeSkillStatus]] = {
        RuntimeSkillStatus.DRAFT: frozenset(
            {
                RuntimeSkillStatus.DRAFT,
                RuntimeSkillStatus.TESTING,
                RuntimeSkillStatus.RETIRED,
            }
        ),
        RuntimeSkillStatus.TESTING: frozenset(
            {
                RuntimeSkillStatus.TESTING,
                RuntimeSkillStatus.DRAFT,
                RuntimeSkillStatus.ACTIVE,
                RuntimeSkillStatus.FAILED,
                RuntimeSkillStatus.RETIRED,
            }
        ),
        RuntimeSkillStatus.ACTIVE: frozenset(
            {
                RuntimeSkillStatus.ACTIVE,
                RuntimeSkillStatus.TESTING,
                RuntimeSkillStatus.RETIRED,
            }
        ),
        RuntimeSkillStatus.FAILED: frozenset(
            {
                RuntimeSkillStatus.FAILED,
                RuntimeSkillStatus.DRAFT,
                RuntimeSkillStatus.TESTING,
                RuntimeSkillStatus.RETIRED,
            }
        ),
        RuntimeSkillStatus.RETIRED: frozenset({RuntimeSkillStatus.RETIRED}),
    }

    @classmethod
    def require(cls, current: RuntimeSkillStatus, target: RuntimeSkillStatus) -> None:
        if target not in cls._allowed[current]:
            raise ValueError(
                f"Invalid runtime skill transition: {current.value} -> {target.value}"
            )


class SkillLifecycleService:
    def __init__(self, repository: SkillLifecycleRepository) -> None:
        self.repository = repository

    def list(self, *, limit: int = 1000) -> list[SkillLifecycleItem]:
        learned = [self._learned_item(item) for item in self.repository.list_skills(limit=limit)]
        runtime = [
            self._runtime_item(item)
            for item in self.repository.list_runtime_skills(limit=limit)
        ]
        return sorted(
            (*learned, *runtime),
            key=lambda item: item.updated_at,
            reverse=True,
        )[:limit]

    def status(self, *, limit: int = 1000) -> dict[str, Any]:
        items = self.list(limit=limit)
        return {
            "schema_version": "skill-lifecycle.v1",
            "items": [item.model_dump(mode="json") for item in items],
            "counts": {
                "total": len(items),
                "experience": sum(
                    item.source_kind is SkillSourceKind.EXPERIENCE for item in items
                ),
                "runtime": sum(item.source_kind is SkillSourceKind.RUNTIME for item in items),
                "verified": sum(item.verified for item in items),
                "active": sum(item.active for item in items),
                "executable": sum(item.executable for item in items),
            },
        }

    @staticmethod
    def _learned_item(skill: SkillRecord) -> SkillLifecycleItem:
        return SkillLifecycleItem(
            skill_id=skill.skill_id,
            name=skill.name,
            capability=skill.capability,
            source_kind=SkillSourceKind.EXPERIENCE,
            phase=SkillLifecyclePhase.ACTIVE if skill.active else SkillLifecyclePhase.RETIRED,
            executable=False,
            verified=True,
            active=skill.active,
            source="verified-experience",
            metrics={
                "success_count": skill.success_count,
                "failure_count": skill.failure_count,
                "fingerprint": skill.fingerprint,
            },
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )

    @staticmethod
    def _runtime_item(skill: RuntimeSkillRecord) -> SkillLifecycleItem:
        phase = {
            RuntimeSkillStatus.DRAFT: SkillLifecyclePhase.CANDIDATE,
            RuntimeSkillStatus.TESTING: SkillLifecyclePhase.TESTING,
            RuntimeSkillStatus.ACTIVE: SkillLifecyclePhase.ACTIVE,
            RuntimeSkillStatus.FAILED: SkillLifecyclePhase.FAILED,
            RuntimeSkillStatus.RETIRED: SkillLifecyclePhase.RETIRED,
        }[skill.status]
        return SkillLifecycleItem(
            skill_id=skill.runtime_skill_id,
            name=skill.manifest.name,
            capability=skill.manifest.category,
            source_kind=SkillSourceKind.RUNTIME,
            phase=phase,
            executable=skill.status is RuntimeSkillStatus.ACTIVE,
            verified=skill.status in {RuntimeSkillStatus.ACTIVE, RuntimeSkillStatus.RETIRED},
            active=skill.status is RuntimeSkillStatus.ACTIVE,
            version=skill.manifest.version,
            source=skill.source,
            metrics={
                "activation_count": skill.activation_count,
                "operations": skill.manifest.operations,
                "generated": skill.manifest.generated,
            },
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )

