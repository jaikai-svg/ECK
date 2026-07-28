from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from eck import __version__
from eck.app import Application, build_application
from eck.config import Settings
from eck.domain.enums import ApprovalStatus
from eck.domain.models import TaskCreate
from eck.services.demos import DemoService


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


def create_api(
    settings: Settings | None = None,
    application: Application | None = None,
) -> FastAPI:
    application = application or build_application(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if application.settings.auto_start_kernel:
            await application.kernel.start()
        try:
            yield
        finally:
            await application.kernel.stop(clean=True)

    api = FastAPI(
        title="ECK Digital Life Kernel",
        version=__version__,
        description=(
            "Persistent, verifier-grounded lifecycle runtime. "
            "No outcome becomes learning without external evidence."
        ),
        lifespan=lifespan,
    )
    api.state.application = application
    static_dir = Path(__file__).resolve().parent.parent / "dashboard"
    api.mount("/static", StaticFiles(directory=static_dir), name="static")

    def get_application() -> Application:
        return cast(Application, api.state.application)

    AppDependency = Annotated[Application, Depends(get_application)]

    @api.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @api.get("/health")
    async def health(app: AppDependency) -> dict[str, object]:
        brain = await app.brain.health()
        chain_valid, failed_sequence = app.store.verify_event_chain()
        status = app.kernel.status()
        return {
            "status": "ok" if chain_valid else "degraded",
            "version": __version__,
            "kernel": status.model_dump(mode="json"),
            "brain": brain.model_dump(mode="json"),
            "event_chain": {
                "valid": chain_valid,
                "failed_sequence": failed_sequence,
            },
            "safety": {
                "network_enabled": app.settings.network_enabled,
                "system_file_mutation_enabled": app.settings.system_file_mutation_enabled,
            },
            "memory": {
                "experiences": len(app.store.list_experiences(limit=10000)),
                "knowledge": len(app.store.list_knowledge(limit=10000)),
                "reflections": len(app.store.list_reflections(limit=10000)),
                "skills": len(app.store.list_skills(limit=10000)),
            },
        }

    @api.get("/v1/kernel/status")
    async def kernel_status(app: AppDependency) -> Any:
        return app.kernel.status()

    @api.post("/v1/kernel/start")
    async def kernel_start(app: AppDependency) -> Any:
        await app.kernel.start()
        return app.kernel.status()

    @api.post("/v1/kernel/pause")
    async def kernel_pause(app: AppDependency) -> Any:
        await app.kernel.pause()
        return app.kernel.status()

    @api.post("/v1/kernel/resume")
    async def kernel_resume(app: AppDependency) -> Any:
        await app.kernel.resume()
        return app.kernel.status()

    @api.post("/v1/kernel/sleep")
    async def kernel_sleep(app: AppDependency) -> dict[str, bool]:
        await app.kernel.request_sleep()
        return {"accepted": True}

    @api.get("/v1/capabilities")
    async def capabilities(app: AppDependency) -> dict[str, Any]:
        return {"items": app.registry.list()}

    @api.post("/v1/tasks", status_code=202)
    async def submit_task(create: TaskCreate, app: AppDependency) -> Any:
        return await app.tasks.submit(create)

    @api.get("/v1/tasks")
    async def list_tasks(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_tasks(limit=limit)}

    @api.get("/v1/tasks/{task_id}")
    async def get_task(task_id: str, app: AppDependency) -> Any:
        try:
            return app.store.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/v1/approvals")
    async def list_approvals(app: AppDependency) -> dict[str, Any]:
        return {
            "items": app.store.list_approvals(status=ApprovalStatus.PENDING, limit=100)
        }

    @api.post("/v1/approvals/{approval_id}/decision")
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

    @api.get("/v1/events")
    async def list_events(
        app: AppDependency,
        after_sequence: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        return {
            "items": app.store.list_events(
                after_sequence=after_sequence,
                limit=min(limit, app.settings.max_events_page_size),
            )
        }

    @api.get("/v1/events/export", response_class=PlainTextResponse)
    async def export_events(app: AppDependency) -> str:
        return app.store.export_events_jsonl()

    @api.get("/v1/experiences")
    async def list_experiences(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_experiences(limit=limit)}

    @api.get("/v1/skills")
    async def list_skills(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_skills(limit=limit)}

    @api.get("/v1/knowledge")
    async def list_knowledge(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_knowledge(limit=limit)}

    @api.get("/v1/reflections")
    async def list_reflections(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_reflections(limit=limit)}

    @api.post("/v1/demos/persistence")
    async def demo_persistence(app: AppDependency) -> dict[str, Any]:
        return await DemoService(app).persistence()

    @api.post("/v1/demos/safe-code")
    async def demo_safe_code(app: AppDependency) -> dict[str, Any]:
        return await DemoService(app).safe_code()

    @api.post("/v1/demos/gridworld")
    async def demo_gridworld(app: AppDependency) -> dict[str, Any]:
        return await DemoService(app).gridworld()

    @api.post("/v1/demos/all")
    async def demo_all(app: AppDependency) -> dict[str, Any]:
        return await DemoService(app).all()

    return api


app = create_api()
