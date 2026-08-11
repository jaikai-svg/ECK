from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from eck.api.contracts import CriticalResearchRequest, ResearchCurriculumRequest
from eck.api.dependencies import AppDependency
from eck.domain.enums import BenchmarkSuite
from eck.domain.models import (
    AutonomousActionContext,
    BenchmarkRunCreate,
    ChallengeDraftCreate,
    ObjectiveEvaluationRequest,
    SocialPostObservationCreate,
)
from eck.services.research import ResearchCurriculumService

router = APIRouter()


@router.post("/v1/research/curricula", status_code=202)
async def start_research_curriculum(
    request: ResearchCurriculumRequest,
    app: AppDependency,
) -> dict[str, object]:
    cycles = min(request.cycles, app.settings.academic_research_max_cycles)
    return await ResearchCurriculumService(app).submit(request.topic, cycles)

@router.post("/v1/research/relevance-audit")
async def audit_research_relevance(app: AppDependency) -> dict[str, object]:
    return await ResearchCurriculumService(app).audit_relevance()

@router.post("/v1/research/critical", status_code=202)
async def start_critical_research(
    request: CriticalResearchRequest,
    app: AppDependency,
) -> dict[str, object]:
    if not app.settings.critical_research_enabled:
        raise HTTPException(status_code=409, detail="Critical research is disabled.")
    return await ResearchCurriculumService(app).submit_critical(
        request.topic,
        url=request.url,
        timespan=request.timespan,
    )

@router.get("/v1/research/runs")
async def list_critical_research_runs(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_research_runs(limit=limit)}

@router.get("/v1/research/runs/{run_id}")
async def get_critical_research_run(run_id: str, app: AppDependency) -> Any:
    try:
        return app.store.get_research_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/v1/research/quality")
async def get_critical_research_quality(app: AppDependency) -> dict[str, Any]:
    return app.store.research_quality_metrics(
        window=app.settings.critical_research_quality_window,
        max_inconclusive_ratio=app.settings.critical_research_max_inconclusive_ratio,
    )

@router.post("/v1/challenges/social-engagement", status_code=202)
async def bootstrap_social_challenge(app: AppDependency) -> Any:
    return await app.challenges.bootstrap_social_engagement()

@router.get("/v1/challenges")
async def list_challenges(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_challenges(limit=limit)}

@router.get("/v1/challenges/drafts")
async def list_challenge_drafts(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_challenge_drafts(limit=limit)}

@router.post("/v1/challenges/drafts", status_code=201)
async def create_challenge_draft(
    request: ChallengeDraftCreate,
    app: AppDependency,
) -> Any:
    draft = app.store.add_challenge_draft(request)
    await app.events.publish(
        "ChallengeDraftCreated",
        draft.draft_id,
        {"goal": draft.goal, "status": draft.status},
    )
    return draft

@router.get("/v1/challenges/{challenge_id}")
async def get_challenge(challenge_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        challenge = app.store.get_challenge(challenge_id)
        observations = app.store.list_social_post_observations(challenge_id, limit=100)
        return {"challenge": challenge, "observations": observations}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/v1/challenges/{challenge_id}/plan")
async def replan_challenge(challenge_id: str, app: AppDependency) -> Any:
    try:
        return await app.challenges.plan(challenge_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/v1/challenges/{challenge_id}/social-observations", status_code=201)
async def record_social_observation(
    challenge_id: str,
    request: SocialPostObservationCreate,
    app: AppDependency,
) -> Any:
    try:
        return await app.challenges.record_social_observation(challenge_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/evaluations")
async def evaluation_dashboard(app: AppDependency) -> dict[str, Any]:
    return app.evaluations.dashboard()

@router.post("/v1/evaluations/runs", status_code=201)
async def record_evaluation(
    request: BenchmarkRunCreate,
    app: AppDependency,
) -> Any:
    try:
        return await app.evaluations.record(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/v1/evaluations/objective", status_code=201)
async def run_objective_evaluation(
    request: ObjectiveEvaluationRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.evaluations.run_objective(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.get("/v1/evaluations/compare")
async def compare_evaluations(
    app: AppDependency,
    suite: Annotated[BenchmarkSuite, Query()] = BenchmarkSuite.ECK_P3_OBJECTIVE,
) -> dict[str, Any]:
    return app.evaluations.compare(suite)

@router.post("/v1/governance/autonomous-actions/evaluate")
async def evaluate_autonomous_action(
    request: AutonomousActionContext,
    app: AppDependency,
) -> Any:
    return app.autonomy.evaluate(request)


