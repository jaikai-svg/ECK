from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from eck.api.contracts import ApprovalDecisionRequest
from eck.api.dependencies import AppDependency
from eck.domain.enums import ApprovalStatus
from eck.domain.models import TaskCreate
from eck.services.demos import DemoService

router = APIRouter()


@router.post("/v1/tasks", status_code=202)
async def submit_task(create: TaskCreate, app: AppDependency) -> Any:
    return await app.tasks.submit(create)

@router.get("/v1/tasks")
async def list_tasks(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_tasks(limit=limit)}

@router.get("/v1/tasks/{task_id}")
async def get_task(task_id: str, app: AppDependency) -> Any:
    try:
        return app.store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/v1/approvals")
async def list_approvals(app: AppDependency) -> dict[str, Any]:
    return {
        "items": app.store.list_approvals(status=ApprovalStatus.PENDING, limit=100)
    }

@router.post("/v1/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    request: ApprovalDecisionRequest,
    app: AppDependency,
) -> Any:
    try:
        decision = ApprovalStatus(request.decision)
        return await app.tasks.decide_approval(approval_id, decision)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/events")
async def list_events(
    app: AppDependency,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    latest: bool = False,
) -> dict[str, Any]:
    page_size = min(limit, app.settings.max_events_page_size)
    return {
        "items": (
            app.store.list_recent_events(limit=page_size)
            if latest
            else app.store.list_events(
                after_sequence=after_sequence,
                limit=page_size,
            )
        )
    }

@router.get("/v1/events/export", response_class=PlainTextResponse)
async def export_events(app: AppDependency) -> str:
    return app.store.export_events_jsonl()

@router.get("/v1/experiences")
async def list_experiences(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_experiences(limit=limit)}

@router.get("/v1/skills")
async def list_skills(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_skills(limit=limit)}

@router.get("/v1/skills/lifecycle")
async def skill_lifecycle(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
) -> dict[str, Any]:
    return app.skill_lifecycle.status(limit=limit)

@router.get("/v1/knowledge")
async def list_knowledge(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_knowledge(limit=limit)}

@router.get("/v1/reflections")
async def list_reflections(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": app.store.list_reflections(limit=limit)}

@router.post("/v1/demos/persistence")
async def demo_persistence(app: AppDependency) -> dict[str, Any]:
    return await DemoService(app).persistence()

@router.post("/v1/demos/safe-code")
async def demo_safe_code(app: AppDependency) -> dict[str, Any]:
    return await DemoService(app).safe_code()

@router.post("/v1/demos/gridworld")
async def demo_gridworld(app: AppDependency) -> dict[str, Any]:
    return await DemoService(app).gridworld()

@router.post("/v1/demos/all")
async def demo_all(app: AppDependency) -> dict[str, Any]:
    return await DemoService(app).all()


