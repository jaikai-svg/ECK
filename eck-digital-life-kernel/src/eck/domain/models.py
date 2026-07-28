from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import (
    ApprovalStatus,
    ComparisonOperator,
    EvidenceSource,
    KernelPhase,
    RiskLevel,
    TaskStatus,
    VerificationStatus,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class VerificationCheck(FrozenModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(
        description="Dot-separated path in capability output, for example metrics.passed."
    )
    operator: ComparisonOperator
    expected: Any = None
    weight: float = Field(default=1.0, gt=0, le=100)


class ForbiddenCondition(FrozenModel):
    name: str = Field(min_length=1, max_length=120)
    path: str
    operator: ComparisonOperator
    expected: Any = None


class SuccessContract(FrozenModel):
    contract_id: str = Field(default_factory=lambda: new_id("contract"))
    schema_version: str = "success-contract.v1"
    goal: str = Field(min_length=3, max_length=2000)
    checks: tuple[VerificationCheck, ...] = Field(min_length=1)
    forbidden_conditions: tuple[ForbiddenCondition, ...] = ()
    required_evidence: tuple[EvidenceSource, ...] = ()
    minimum_score: float = Field(default=1.0, ge=0, le=1)
    max_attempts: int = Field(default=3, ge=1, le=100)
    max_cost_units: float = Field(default=1000, gt=0)
    require_reproducible: bool = True
    reversible_exploration_only: bool = True
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_external_evidence(self) -> SuccessContract:
        if self.required_evidence and not any(x.is_external for x in self.required_evidence):
            raise ValueError("At least one required evidence source must be external to the model.")
        return self


class Evidence(FrozenModel):
    evidence_id: str = Field(default_factory=lambda: new_id("evidence"))
    source: EvidenceSource
    claim: str
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)


class ActionProposal(FrozenModel):
    action_id: str = Field(default_factory=lambda: new_id("action"))
    capability: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    declared_risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    estimated_cost_units: float = Field(default=1, ge=0)


class CapabilityResult(FrozenModel):
    action_id: str
    capability: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    reversible: bool = True
    cost_units: float = Field(default=0, ge=0)
    started_at: datetime
    finished_at: datetime


class VerificationReport(FrozenModel):
    report_id: str = Field(default_factory=lambda: new_id("verification"))
    status: VerificationStatus
    score: float = Field(ge=0, le=1)
    passed_checks: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    violated_constraints: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    external_evidence_present: bool = False
    reproducible: bool = False
    reason: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class TaskCreate(FrozenModel):
    goal: str = Field(min_length=3, max_length=2000)
    success_contract: SuccessContract
    action: ActionProposal
    labels: tuple[str, ...] = ()


class TaskRecord(FrozenModel):
    task_id: str
    goal: str
    status: TaskStatus
    risk_level: RiskLevel
    success_contract: SuccessContract
    action: ActionProposal
    attempts: int = 0
    result: CapabilityResult | None = None
    verification: VerificationReport | None = None
    created_at: datetime
    updated_at: datetime


class PolicyDecision(FrozenModel):
    allowed: bool
    requires_approval: bool
    risk_level: RiskLevel
    reasons: tuple[str, ...]


class ApprovalRecord(FrozenModel):
    approval_id: str
    task_id: str
    action: ActionProposal
    status: ApprovalStatus
    reason: str
    created_at: datetime
    decided_at: datetime | None = None


class KernelStatus(FrozenModel):
    identity: str
    phase: KernelPhase
    boot_count: int
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    pending_tasks: int
    pending_approvals: int
    event_count: int


class ExperienceRecord(FrozenModel):
    experience_id: str
    task_id: str
    capability: str
    outcome: VerificationStatus
    summary: str
    evidence_ids: tuple[str, ...]
    admitted: bool
    admission_reason: str
    created_at: datetime


class KnowledgeRecord(FrozenModel):
    knowledge_id: str
    task_id: str
    capability: str
    claim: str
    outcome: VerificationStatus
    evidence_ids: tuple[str, ...]
    externally_grounded: bool
    reproducible: bool
    admitted: bool
    created_at: datetime


class ReflectionRecord(FrozenModel):
    reflection_id: str
    task_id: str
    capability: str
    outcome: VerificationStatus
    observation: str
    lesson: str
    next_step: str
    verification_report_id: str
    evidence_ids: tuple[str, ...]
    generator: str
    created_at: datetime


class SkillRecord(FrozenModel):
    skill_id: str
    fingerprint: str
    name: str
    capability: str
    procedure: dict[str, Any]
    verification_basis: dict[str, Any]
    success_count: int
    failure_count: int
    active: bool
    created_at: datetime
    updated_at: datetime


class EventRecord(FrozenModel):
    sequence: int
    event_id: str
    event_type: str
    aggregate_id: str
    correlation_id: str | None
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime
