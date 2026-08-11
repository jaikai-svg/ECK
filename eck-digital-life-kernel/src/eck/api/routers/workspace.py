from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from eck.api.dependencies import AppDependency
from eck.domain.enums import MissionStatus
from eck.modules.skills.lifecycle import SkillLifecyclePhase
from eck.services.workspace import WorkspaceReadService

router = APIRouter(tags=["workspace"])


@router.get("/v1/workspace/home")
def workspace_home(app: AppDependency) -> dict[str, Any]:
    return WorkspaceReadService(app).home()


@router.get("/v1/workspace/system")
def workspace_system(app: AppDependency) -> dict[str, Any]:
    return WorkspaceReadService(app).system()


@router.get("/v1/workspace/projects")
def workspace_projects(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=48)] = 12,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    status: MissionStatus | None = None,
) -> dict[str, Any]:
    return WorkspaceReadService(app).projects(
        limit=limit,
        offset=offset,
        status=status,
    )


@router.get("/v1/workspace/projects/{project_id}")
def workspace_project(project_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return WorkspaceReadService(app).project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/workspace/library")
def workspace_library(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=48)] = 24,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
) -> dict[str, Any]:
    return app.library.page(limit=limit, offset=offset, query=q)


@router.get("/v1/workspace/skills")
def workspace_skills(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=48)] = 24,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    phase: SkillLifecyclePhase | None = None,
) -> dict[str, Any]:
    return WorkspaceReadService(app).skills(
        limit=limit,
        offset=offset,
        phase=phase,
    )
