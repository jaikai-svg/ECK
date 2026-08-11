from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from eck import __version__
from eck.api.routers.experimental_p6 import router as experimental_p6_router
from eck.api.routers.experimental_p7 import router as experimental_p7_router
from eck.api.routers.learning_evolution import router as learning_evolution_router
from eck.api.routers.records import router as records_router
from eck.api.routers.research_governance import router as research_governance_router
from eck.api.routers.runtime import router as runtime_router
from eck.api.routers.system import router as system_router
from eck.api.routers.workspace import router as workspace_router
from eck.api.routers.workspace_phase2 import router as workspace_phase2_router
from eck.app import Application, build_application
from eck.config import Settings
from eck.runtime.shutdown import clear_shutdown_request


def create_api(
    settings: Settings | None = None,
    application: Application | None = None,
) -> FastAPI:
    application = application or build_application(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        clear_shutdown_request()
        await application.local_services.ensure_ollama()
        if application.settings.auto_start_kernel:
            await application.kernel.start()
        try:
            yield
        finally:
            await application.kernel.stop(clean=True)
            await application.image_generation.close()
            await application.image_background_removal.close()
            application.rag.close()
            await application.local_services.stop_owned()
            clear_shutdown_request()

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
    api.include_router(experimental_p6_router)
    api.include_router(experimental_p7_router)
    api.include_router(system_router)
    api.include_router(learning_evolution_router)
    api.include_router(runtime_router)
    api.include_router(research_governance_router)
    api.include_router(records_router)
    api.include_router(workspace_router)
    api.include_router(workspace_phase2_router)
    static_dir = Path(__file__).resolve().parent.parent / "dashboard"
    mimetypes.add_type("text/css", ".css")
    api.mount("/static", StaticFiles(directory=static_dir), name="static")
    api.mount(
        "/artifacts",
        StaticFiles(directory=application.settings.image_output_dir),
        name="artifacts",
    )
    api.mount(
        "/video-artifacts",
        StaticFiles(directory=application.settings.video_output_dir),
        name="video-artifacts",
    )

    @api.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return api


app = create_api()
