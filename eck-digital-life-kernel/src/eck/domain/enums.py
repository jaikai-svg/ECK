from __future__ import annotations

from enum import StrEnum


class KernelPhase(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    SLEEPING = "sleeping"
    STOPPING = "stopping"
    FAULTED = "faulted"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNVERIFIABLE = "unverifiable"
    CONSTRAINT_VIOLATION = "constraint_violation"
    BLOCKED = "blocked"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }[self]


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class EvidenceSource(StrEnum):
    ENVIRONMENT = "environment"
    UNIT_TEST = "unit_test"
    FORMAL_CHECK = "formal_check"
    HUMAN = "human"
    TOOL = "tool"
    MODEL_SELF_REPORT = "model_self_report"

    @property
    def is_external(self) -> bool:
        return self is not EvidenceSource.MODEL_SELF_REPORT


class VerificationStatus(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNVERIFIABLE = "unverifiable"
    CONSTRAINT_VIOLATION = "constraint_violation"


class ComparisonOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    TRUTHY = "truthy"

