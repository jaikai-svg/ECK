from __future__ import annotations

import zipfile
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from eck.api.contracts import ChatRequest, CognitiveBundleRequest
from eck.api.dependencies import AppDependency
from eck.domain.models import SkillForgeRequest
from eck.services.dialogue import DialogueService

router = APIRouter()


@router.post("/v1/portability/bundles", status_code=201)
async def export_cognitive_bundle(
    request: CognitiveBundleRequest,
    app: AppDependency,
) -> dict[str, Any]:
    return await app.portability.export(include_artifacts=request.include_artifacts)

@router.get("/v1/portability/bundles/{archive_name}")
async def download_cognitive_bundle(
    archive_name: str,
    app: AppDependency,
) -> FileResponse:
    try:
        return FileResponse(
            app.portability.bundle_path(archive_name),
            media_type="application/zip",
            filename=archive_name,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/v1/portability/bundles/{archive_name}/verify")
async def verify_cognitive_bundle(
    archive_name: str,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.portability.verify(archive_name)
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/runtime/status")
async def runtime_status(app: AppDependency) -> dict[str, Any]:
    forge = await app.forge.status()
    return {
        "version": app.versions.status(),
        "skill_runtime": forge,
        "scheduler": {
            "autonomous_learning_percent": app.settings.autonomous_learning_percent,
            "challenge_execution_percent": app.settings.challenge_execution_percent,
        },
    }

@router.post("/v1/runtime/worker/build")
async def build_skill_worker(app: AppDependency) -> dict[str, Any]:
    return await app.forge.build_worker()

@router.get("/v1/runtime/skills")
async def list_runtime_skills(app: AppDependency) -> dict[str, Any]:
    return {"items": app.store.list_runtime_skills(limit=500)}

@router.post("/v1/runtime/skills/forge", status_code=202)
async def forge_runtime_skill(request: SkillForgeRequest, app: AppDependency) -> Any:
    try:
        return await app.forge.forge(request)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/v1/runtime/skills/validate")
async def validate_runtime_skills(app: AppDependency) -> dict[str, Any]:
    return {"items": await app.forge.validate_pending()}

@router.post("/v1/runtime/skills/{runtime_skill_id}/validate")
async def validate_runtime_skill(runtime_skill_id: str, app: AppDependency) -> Any:
    try:
        return await app.forge.validate_skill(runtime_skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/v1/runtime/skills/{runtime_skill_id}/repair", status_code=202)
async def repair_runtime_skill(runtime_skill_id: str, app: AppDependency) -> Any:
    try:
        return await app.forge.repair_failed_skill(runtime_skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, SyntaxError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/v1/chat/commands")
async def chat_commands() -> dict[str, Any]:
    return {"items": DialogueService.command_catalog()}

@router.post("/v1/chat")
async def chat(request: ChatRequest, app: AppDependency) -> dict[str, Any]:
    try:
        return await DialogueService(app).respond(
            request.message,
            [item.model_dump() for item in request.history],
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


