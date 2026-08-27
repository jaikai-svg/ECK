from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from eck.api.contracts import (
    EvolutionActivationRequest,
    EvolutionApprovalRequest,
    EvolutionHeldoutPackRequest,
    EvolutionOpportunityAttachPackRequest,
    EvolutionRollbackRequest,
    LearningThemeStateRequest,
)
from eck.api.dependencies import AppDependency
from eck.domain.models import (
    CoreCandidateRequest,
    DevelopmentProjectRequest,
    GuidedSkillAcquisitionRequest,
    LearningThemeCreate,
)
from eck.runtime.shutdown import request_restart

router = APIRouter()


@router.get("/v1/supervisor/status")
async def supervisor_status(app: AppDependency) -> dict[str, Any]:
    return app.supervisor.status()

@router.get("/v1/learning/autonomous/status")
async def autonomous_learning_status(app: AppDependency) -> dict[str, Any]:
    return app.autonomous_learning.status()

@router.get("/v1/learning/community-sources")
async def community_learning_sources(app: AppDependency) -> dict[str, Any]:
    return app.community_sources.status()

@router.get("/v1/learning/themes")
async def list_learning_themes(app: AppDependency) -> dict[str, Any]:
    return {
        "items": app.store.list_learning_themes(limit=100),
        "eck_focus_percent": app.settings.autonomous_eck_focus_percent,
        "theme_focus_percent": app.settings.p5_exploration_percent,
        "portfolio": app.autonomous_learning.portfolio(),
    }

@router.post("/v1/learning/themes", status_code=201)
async def create_learning_theme(
    request: LearningThemeCreate,
    app: AppDependency,
) -> Any:
    theme = app.store.add_learning_theme(request)
    await app.events.publish(
        "LearningThemeCreated",
        theme.theme_id,
        {"title": theme.title, "active": theme.active},
    )
    return theme

@router.patch("/v1/learning/themes/{theme_id}")
async def update_learning_theme(
    theme_id: str,
    request: LearningThemeStateRequest,
    app: AppDependency,
) -> Any:
    try:
        theme = app.store.set_learning_theme_active(theme_id, active=request.active)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await app.events.publish(
        "LearningThemeUpdated",
        theme.theme_id,
        {"title": theme.title, "active": theme.active},
    )
    return theme

@router.delete("/v1/learning/themes/{theme_id}", status_code=204)
async def delete_learning_theme(theme_id: str, app: AppDependency) -> None:
    try:
        app.store.delete_learning_theme(theme_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await app.events.publish("LearningThemeDeleted", theme_id, {})

@router.get("/v1/learning/skill-tree")
async def learning_skill_tree(app: AppDependency) -> dict[str, Any]:
    return app.skill_graph.build()

@router.get("/v1/learning/skill-tree/search")
async def search_learning_skill_tree(
    app: AppDependency,
    query: str = Query(alias="q", min_length=2, max_length=200),
    limit: int = Query(default=8, ge=1, le=30),
) -> dict[str, Any]:
    return {"items": app.skill_graph.search(query, limit=limit)}

@router.get("/v1/evolution/status")
async def evolution_status(app: AppDependency) -> dict[str, Any]:
    return await app.evolution.status()


@router.get("/v1/evolution/opportunities")
async def list_evolution_opportunities(
    app: AppDependency,
    limit: int = Query(default=100, ge=1, le=500),
    status: str | None = Query(default=None, min_length=3, max_length=80),
) -> dict[str, Any]:
    return {
        "status": app.evolution_director.status(),
        "items": app.evolution_director.list_opportunities(limit=limit, status=status),
    }


@router.get("/v1/evolution/opportunities/{opportunity_id}")
async def get_evolution_opportunity(
    opportunity_id: str,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.evolution_director.get(opportunity_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/v1/evolution/opportunities/scan")
async def scan_evolution_opportunities(app: AppDependency) -> dict[str, Any]:
    return await app.evolution_director.scan()


@router.post("/v1/evolution/opportunities/{opportunity_id}/attach-pack")
async def attach_evolution_opportunity_pack(
    opportunity_id: str,
    request: EvolutionOpportunityAttachPackRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.evolution_director.attach_pack(opportunity_id, request.pack_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/evolution/opportunities/{opportunity_id}/run", status_code=202)
async def run_evolution_opportunity(
    opportunity_id: str,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.evolution_director.run_opportunity(opportunity_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, OSError, RuntimeError, SyntaxError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/identity/soul")
async def identity_soul(app: AppDependency) -> dict[str, Any]:
    return app.identity_service.status()

@router.get("/v1/self-model")
async def repository_self_model(app: AppDependency) -> dict[str, Any]:
    return app.self_model.status()

@router.post("/v1/self-model/refresh")
async def refresh_repository_self_model(app: AppDependency) -> dict[str, Any]:
    return app.self_model.refresh()

@router.get("/v1/self-model/impact")
async def repository_change_impact(
    app: AppDependency,
    path: str = Query(min_length=3, max_length=500),
) -> dict[str, Any]:
    try:
        return app.self_model.impact(path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/v1/evolution/skill-bridge")
async def research_skill_bridge_status(app: AppDependency) -> dict[str, Any]:
    return await app.skill_bridge.status()

@router.post("/v1/evolution/skill-bridge/run", status_code=202)
async def run_research_skill_bridge(app: AppDependency) -> dict[str, Any]:
    try:
        return await app.skill_bridge.run_if_needed(force=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/evolution/tool-campaign")
async def tool_campaign_status(app: AppDependency) -> dict[str, Any]:
    return app.tool_campaign.status()

@router.post("/v1/evolution/tool-campaign/run", status_code=202)
async def run_tool_campaign(app: AppDependency) -> dict[str, Any]:
    try:
        return await app.tool_campaign.run_once(force=True)
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/v1/learning/skills/acquire", status_code=202)
async def acquire_guided_skill(
    request: GuidedSkillAcquisitionRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.skill_bridge.acquire(request)
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/evolution/core-candidates")
async def list_core_candidates(app: AppDependency) -> dict[str, Any]:
    return {"status": app.core_lab.status(), "items": app.core_lab.list_candidates()}

@router.get("/v1/evolution/core-candidates/{candidate_id}")
async def get_core_candidate(candidate_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.core_lab.get_candidate(candidate_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/v1/evolution/core-candidates", status_code=201)
async def create_core_candidate(
    request: CoreCandidateRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.core_lab.create_candidate(request)
    except (FileNotFoundError, OSError, RuntimeError, SyntaxError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/v1/evolution/core-candidates/{candidate_id}/validate")
async def validate_core_candidate(
    candidate_id: str,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.core_lab.validate_candidate(candidate_id)
    except (FileNotFoundError, KeyError, OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/evolution/transactions")
async def list_evolution_transactions(
    app: AppDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    return {
        "status": app.evolution_transactions.status(),
        "items": app.evolution_transactions.list_transactions(limit=limit),
    }


@router.get("/v1/evolution/transactions/{transaction_id}")
async def get_evolution_transaction(
    transaction_id: str,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.evolution_transactions.get(transaction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/evolution/heldout-packs")
async def list_evolution_heldout_packs(app: AppDependency) -> dict[str, Any]:
    return {"items": app.evolution_transactions.list_heldout_packs()}


@router.post("/v1/evolution/heldout-packs", status_code=201)
async def register_evolution_heldout_pack(
    request: EvolutionHeldoutPackRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.evolution_transactions.register_heldout_pack(
            pack_id=request.pack_id,
            description=request.description,
            test_files=request.test_files,
            change_kind=request.change_kind,
            minimum_speedup_percent=request.minimum_speedup_percent,
            allow_non_regression=request.allow_non_regression,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/evolution/core-candidates/{candidate_id}/evaluate")
async def evaluate_core_candidate(
    candidate_id: str,
    app: AppDependency,
    pack_id: str = Query(min_length=3, max_length=80),
) -> dict[str, Any]:
    try:
        return await app.evolution_transactions.evaluate(candidate_id, pack_id)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/evolution/core-candidates/{candidate_id}/approve")
async def approve_core_candidate(
    candidate_id: str,
    request: EvolutionApprovalRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.evolution_transactions.approve(
            candidate_id,
            approved_by=request.approved_by,
            reason=request.reason,
            confirmed_candidate_tree_sha=request.confirmed_candidate_tree_sha,
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/evolution/core-candidates/{candidate_id}/activate", status_code=202)
async def activate_core_candidate(
    candidate_id: str,
    request: EvolutionActivationRequest,
    background_tasks: BackgroundTasks,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        result = await app.evolution_transactions.activate(
            candidate_id,
            confirmed_candidate_tree_sha=request.confirmed_candidate_tree_sha,
            reason=request.reason,
        )
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(request_restart)
    return result


@router.post("/v1/evolution/transactions/{transaction_id}/rollback", status_code=202)
async def rollback_evolution_transaction(
    transaction_id: str,
    request: EvolutionRollbackRequest,
    background_tasks: BackgroundTasks,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        result = await app.evolution_transactions.rollback(
            transaction_id,
            reason=request.reason,
        )
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(request_restart)
    return result

@router.get("/v1/evolution/projects")
async def list_autonomous_projects(app: AppDependency) -> dict[str, Any]:
    return {"status": await app.project_lab.status(), "items": app.project_lab.list_projects()}

@router.get("/v1/evolution/projects/{project_id}")
async def get_autonomous_project(project_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.project_lab.get_project(project_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/v1/evolution/projects", status_code=201)
async def create_autonomous_project(
    request: DevelopmentProjectRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.project_lab.create(request)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/v1/evolution/projects/run", status_code=202)
async def run_autonomous_project_cycle(app: AppDependency) -> dict[str, Any]:
    try:
        return await app.project_lab.run_if_needed(force=True)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/v1/evolution/projects/{project_id}/publish")
async def publish_autonomous_project(project_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return await app.project_lab.publish(project_id)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
