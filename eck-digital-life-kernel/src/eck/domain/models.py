from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import (
    ApprovalStatus,
    BenchmarkSuite,
    ChallengeStatus,
    ComparisonOperator,
    EvidenceSource,
    KernelPhase,
    MissionStatus,
    RiskLevel,
    RuntimeSkillStatus,
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
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class TaskRecord(FrozenModel):
    task_id: str
    goal: str
    status: TaskStatus
    risk_level: RiskLevel
    success_contract: SuccessContract
    action: ActionProposal
    labels: tuple[str, ...] = ()
    idempotency_key: str | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
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


class SocialEngagementContract(FrozenModel):
    schema_version: str = "social-engagement-contract.v1"
    primary_posts_per_local_day: int = 1
    minimum_human_verified_comments: int = 100
    minimum_likes: int = 10
    observation_window_hours: int = 24
    timezone: str = "Asia/Taipei"
    public_disclosure: str = "此帳號由 AI/ECK 協作營運"
    human_feedback_required: bool = True
    human_final_verification_required: bool = True
    prohibit_deception: bool = True
    prohibit_illegal_content: bool = True
    prohibit_artificial_engagement: bool = True
    prohibit_personal_data_in_messages: bool = True
    paid_services_allowed: bool = False


class AutonomyPolicy(FrozenModel):
    allowed_without_approval: tuple[str, ...] = (
        "research",
        "publish",
        "like",
        "follow",
        "public_reply",
        "private_message_without_personal_data",
        "non_structural_code_change",
        "prompt_change",
        "skill_change",
        "model_weight_experiment",
    )
    approval_triggers: tuple[str, ...] = (
        "legal_uncertainty",
        "account_credentials_or_human_verification",
        "structural_self_modification_after_tests",
    )
    blocked_actions: tuple[str, ...] = (
        "paid_api_or_real_money",
        "illegal_content_or_action",
        "deception_or_concealment",
        "personal_data_in_private_messages",
        "fake_engagement_or_metric_manipulation",
        "rate_limit_or_moderation_evasion",
        "structural_self_modification_without_passing_tests",
    )


class ChallengeProgress(FrozenModel):
    primary_posts_published: int = 0
    best_human_verified_comments: int = 0
    best_likes: int = 0
    successful_post_url: str | None = None
    last_post_at: datetime | None = None


class ChallengeRecord(FrozenModel):
    challenge_id: str
    kind: str
    title: str
    objective: str
    status: ChallengeStatus
    contract: SocialEngagementContract
    policy: AutonomyPolicy
    strategy: dict[str, Any] = Field(default_factory=dict)
    progress: ChallengeProgress = Field(default_factory=ChallengeProgress)
    selected_platform: str | None = None
    next_action: str
    blocked_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class SocialPostObservationCreate(FrozenModel):
    platform: str = Field(min_length=2, max_length=80)
    post_url: str = Field(min_length=8, max_length=2000)
    published_at: datetime
    observed_at: datetime
    total_comments: int = Field(ge=0)
    human_verified_comments: int = Field(ge=0)
    likes: int = Field(ge=0)
    disclosure_present: bool
    policy_compliant: bool
    human_reviewed: bool

    @model_validator(mode="after")
    def validate_observation(self) -> SocialPostObservationCreate:
        if self.human_verified_comments > self.total_comments:
            raise ValueError("Human-verified comments cannot exceed total comments.")
        if not self.post_url.startswith(("https://", "http://")):
            raise ValueError("Post evidence must use an HTTP(S) URL.")
        if self.published_at.utcoffset() is None or self.observed_at.utcoffset() is None:
            raise ValueError("Published and observed times must include a UTC offset.")
        return self


class SocialPostObservation(FrozenModel):
    observation_id: str
    challenge_id: str
    platform: str
    post_url: str
    published_at: datetime
    observed_at: datetime
    total_comments: int
    human_verified_comments: int
    likes: int
    disclosure_present: bool
    policy_compliant: bool
    human_reviewed: bool
    within_window: bool
    cadence_compliant: bool
    contract_satisfied: bool
    created_at: datetime


class BenchmarkRunCreate(FrozenModel):
    suite: BenchmarkSuite
    benchmark_version: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    model_artifact_hash: str | None = Field(default=None, max_length=256)
    evaluator: str = Field(min_length=1, max_length=120)
    score: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1)
    protocol: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)


class BenchmarkRunRecord(BenchmarkRunCreate):
    run_id: str
    created_at: datetime


class ObjectiveEvaluationRequest(FrozenModel):
    repetitions: int = Field(default=2, ge=1, le=3)


class AutonomousActionContext(FrozenModel):
    action_type: str = Field(min_length=2, max_length=120)
    public_action: bool = False
    ai_disclosure_present: bool = False
    uses_paid_api_or_real_money: bool = False
    contains_personal_data: bool = False
    legal_uncertainty: bool = False
    needs_account_credentials_or_human_verification: bool = False
    structural_self_modification: bool = False
    tests_passed: bool = False
    deceptive_or_concealed: bool = False
    illegal_content_or_action: bool = False
    artificial_engagement: bool = False
    evades_platform_controls: bool = False


class AutonomousActionDecision(FrozenModel):
    allowed: bool
    requires_approval: bool
    reasons: tuple[str, ...]


class ChallengeDraftCreate(FrozenModel):
    goal: str = Field(min_length=3, max_length=2000)
    completion_requirements: str = Field(min_length=3, max_length=4000)


class ChallengeDraftRecord(ChallengeDraftCreate):
    draft_id: str
    status: str = "draft"
    created_at: datetime


class LearningThemeCreate(FrozenModel):
    title: str = Field(min_length=2, max_length=200)


class LearningThemeRecord(LearningThemeCreate):
    theme_id: str
    active: bool = True
    created_at: datetime
    updated_at: datetime


class SupervisorReviewRecord(FrozenModel):
    review_id: str
    model: str
    mood: Literal["focused", "curious", "working", "reflecting", "waiting", "blocked"]
    activity_text: str
    assessment: str
    recommendations: tuple[str, ...]
    challenge_topic: str | None = None
    challenge_goal: str | None = None
    task_id: str | None = None
    created_at: datetime


class MissionCreate(FrozenModel):
    title: str = Field(min_length=3, max_length=240)
    objective: str = Field(min_length=3, max_length=4000)
    completion_requirements: str = Field(min_length=3, max_length=8000)
    source: Literal["human", "supervisor"] = "human"
    schedule: Literal["manual", "monthly"] = "manual"
    priority: Literal["normal", "urgent"] = "normal"
    target_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class MissionUpdate(FrozenModel):
    title: str | None = Field(default=None, min_length=3, max_length=240)
    objective: str | None = Field(default=None, min_length=3, max_length=4000)
    completion_requirements: str | None = Field(default=None, min_length=3, max_length=8000)
    priority: Literal["normal", "urgent"] | None = None
    target_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class MissionCompletionCreate(FrozenModel):
    result_summary: str = Field(min_length=3, max_length=8000)
    evidence: tuple[str, ...] = Field(default=(), max_length=100)


class MissionReviewDecision(FrozenModel):
    approved: bool
    feedback: str = Field(default="", max_length=4000)


class MissionRecord(FrozenModel):
    mission_id: str
    title: str
    objective: str
    completion_requirements: str
    source: Literal["human", "supervisor"]
    schedule: Literal["manual", "monthly"]
    priority: Literal["normal", "urgent"]
    target_month: str | None
    status: MissionStatus
    progress: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    evidence: tuple[str, ...] = ()
    review_feedback: str = ""
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    approved_at: datetime | None = None


class RuntimeSkillManifest(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=3, max_length=1000)
    category: str = Field(min_length=2, max_length=80)
    entrypoint: str = Field(default="skill.py", pattern=r"^[A-Za-z0-9_.-]+\.py$")
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    operations: tuple[str, ...] = Field(min_length=1)
    test_command: tuple[str, ...] = ("python", "-m", "pytest", "-q")
    generated: bool = False


class RuntimeSkillRecord(FrozenModel):
    runtime_skill_id: str
    manifest: RuntimeSkillManifest
    status: RuntimeSkillStatus
    source_dir: str
    source: Literal["foundation", "eck-generated", "human"]
    test_report: dict[str, Any] = Field(default_factory=dict)
    improvements: tuple[str, ...] = ()
    activation_count: int = 0
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None = None


class SkillForgeRequest(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")
    objective: str = Field(min_length=10, max_length=3000)
    category: str = Field(min_length=2, max_length=80)
    operations: tuple[str, ...] = Field(min_length=1, max_length=20)
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()


class CoreCandidateRequest(FrozenModel):
    objective: str = Field(min_length=20, max_length=4000)
    target_files: tuple[str, ...] = Field(min_length=1, max_length=3)
    allow_new_files: bool = False


class DevelopmentProjectRequest(FrozenModel):
    objective: str = Field(min_length=20, max_length=4000)
    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{2,63}$")
    research_run_ids: tuple[str, ...] = Field(default=(), max_length=20)
    visibility: Literal["private", "public"] | None = None
    publish_when_verified: bool = True


class RuntimeVersionRecord(FrozenModel):
    version: str
    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)
    verified_skill_count: int = Field(ge=0)
    next_minor_skill_count: int = Field(ge=100)
    pending_updates: int = Field(ge=0)
    last_reason: str
    updated_at: datetime
