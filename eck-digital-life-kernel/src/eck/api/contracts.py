from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


class ResearchCurriculumRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    cycles: int = Field(default=2, ge=1, le=8)


class CriticalResearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    url: str | None = Field(default=None, min_length=10, max_length=2000)
    timespan: str | None = Field(
        default=None,
        pattern=r"^\d{1,3}(?:min|h|d|w|m)$",
    )


class CognitiveBundleRequest(BaseModel):
    include_artifacts: bool = False


class LearningThemeStateRequest(BaseModel):
    active: bool


class EvolutionPackExportRequest(BaseModel):
    runtime_skill_id: str = Field(min_length=10, max_length=100)
    license_spdx: str = Field(min_length=3, max_length=64)
    source_url: str = Field(default="", max_length=2000)


class EvolutionPackPlanRequest(BaseModel):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvolutionPackRecordsRequest(BaseModel):
    record_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    license_spdx: str = Field(min_length=3, max_length=64)
    source_url: str = Field(default="", max_length=2000)


class EvolutionPackMissionRequest(BaseModel):
    mission_id: str = Field(min_length=10, max_length=100)
    license_spdx: str = Field(min_length=3, max_length=64)
    source_url: str = Field(default="", max_length=2000)


class FederationCommunityReviewRequest(BaseModel):
    reviewer_node_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["approve", "reject"]
    reproduction_success: bool
    fixed_test_delta: float = Field(ge=-1, le=1)
    hidden_test_regression: bool
    permission_reviewed: bool
    dependency_reviewed: bool
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    notes: str = Field(default="", max_length=2000)


class FederationRevocationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
