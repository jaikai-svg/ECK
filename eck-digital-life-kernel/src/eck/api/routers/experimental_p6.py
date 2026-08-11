from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from eck.api.dependencies import AppDependency
from eck.domain.models import (
    MissionCompletionCreate,
    MissionCreate,
    MissionReviewDecision,
    MissionUpdate,
)

router = APIRouter(tags=["experimental-p6-missions"])


@router.get("/v1/missions")
async def list_missions(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_missions(limit=limit)}


@router.get("/v1/missions/executor/status")
async def mission_executor_status(app: AppDependency) -> dict[str, Any]:
    return app.mission_executor.status()


@router.post("/v1/missions", status_code=201)
async def create_mission(request: MissionCreate, app: AppDependency) -> Any:
    return await app.missions.create(request)


@router.get("/v1/missions/{mission_id}/execution")
async def mission_execution(mission_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.mission_executor.status(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/v1/missions/{mission_id}/execute-next")
async def execute_next_mission_step(mission_id: str, app: AppDependency) -> Any:
    try:
        app.store.get_mission(mission_id)
        step = await app.mission_executor.run_next()
        if step is not None and step.mission_id != mission_id:
            return {"executed": False, "detail": "A higher-priority mission step ran first."}
        return {"executed": step is not None, "step": step}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/missions/{mission_id}/preview/", include_in_schema=False)
async def mission_preview_index(mission_id: str, app: AppDependency) -> FileResponse:
    try:
        return FileResponse(app.mission_executor.preview_path(mission_id, "index.html"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/v1/missions/{mission_id}/preview/{artifact_path:path}",
    include_in_schema=False,
)
async def mission_preview_artifact(
    mission_id: str,
    artifact_path: str,
    app: AppDependency,
) -> FileResponse:
    try:
        return FileResponse(app.mission_executor.preview_path(mission_id, artifact_path))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/missions/{mission_id}/download", include_in_schema=False)
async def download_mission_package(mission_id: str, app: AppDependency) -> FileResponse:
    try:
        package = app.mission_executor.package_path(mission_id)
        return FileResponse(package, filename=package.name, media_type="application/zip")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/v1/missions/{mission_id}")
async def update_mission(
    mission_id: str,
    request: MissionUpdate,
    app: AppDependency,
) -> Any:
    try:
        return await app.missions.update(mission_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/missions/{mission_id}/completion")
async def submit_mission_completion(
    mission_id: str,
    request: MissionCompletionCreate,
    app: AppDependency,
) -> Any:
    try:
        return await app.missions.submit_completion(mission_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/missions/{mission_id}/review")
async def review_mission(
    mission_id: str,
    request: MissionReviewDecision,
    app: AppDependency,
) -> Any:
    try:
        return await app.missions.review(mission_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/missions/{mission_id}/reopen")
async def reopen_mission(mission_id: str, app: AppDependency) -> Any:
    try:
        return await app.missions.reopen(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/v1/missions/{mission_id}")
async def cancel_mission(mission_id: str, app: AppDependency) -> Any:
    try:
        return await app.missions.cancel(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

