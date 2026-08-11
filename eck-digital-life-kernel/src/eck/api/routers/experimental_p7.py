from __future__ import annotations

import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from eck.api.contracts import (
    EvolutionPackExportRequest,
    EvolutionPackMissionRequest,
    EvolutionPackPlanRequest,
    EvolutionPackRecordsRequest,
    FederationCommunityReviewRequest,
    FederationRevocationRequest,
)
from eck.api.dependencies import AppDependency

router = APIRouter(tags=["experimental-p7-federation"])


@router.get("/v1/federation/status")
async def federation_status(app: AppDependency) -> dict[str, Any]:
    return app.federation.status()


@router.post("/v1/federation/packs", status_code=201)
async def export_evolution_pack(
    request: EvolutionPackExportRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.federation.export_skill(
            request.runtime_skill_id,
            license_spdx=request.license_spdx,
            source_url=request.source_url,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/packs/knowledge", status_code=201)
async def export_knowledge_pack(
    request: EvolutionPackRecordsRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.federation.export_knowledge(
            request.record_ids,
            license_spdx=request.license_spdx,
            source_url=request.source_url,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/packs/strategy", status_code=201)
async def export_strategy_pack(
    request: EvolutionPackMissionRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.federation.export_strategy(
            request.mission_id,
            license_spdx=request.license_spdx,
            source_url=request.source_url,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/packs/evaluation", status_code=201)
async def export_evaluation_pack(
    request: EvolutionPackRecordsRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.federation.export_evaluation(
            request.record_ids,
            license_spdx=request.license_spdx,
            source_url=request.source_url,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/packs/distillation", status_code=201)
async def export_distillation_pack(
    request: EvolutionPackRecordsRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.federation.export_distillation(
            request.record_ids,
            license_spdx=request.license_spdx,
            source_url=request.source_url,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/packs/{archive_name}/sign")
async def sign_evolution_pack(archive_name: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.federation.sign(archive_name)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/federation/packs/{archive_name}")
async def download_evolution_pack(archive_name: str, app: AppDependency) -> FileResponse:
    try:
        return FileResponse(
            app.federation.pack_path(archive_name, location="outbox"),
            media_type="application/zip",
            filename=archive_name,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/federation/inbox/{archive_name}/verify")
async def verify_evolution_pack(archive_name: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.federation.verify(archive_name)
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/federation/inbox/{archive_name}/preview")
async def preview_evolution_pack(archive_name: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.federation.preview(archive_name)
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/inbox/{archive_name}/stage", status_code=202)
async def stage_evolution_pack(
    archive_name: str,
    request: EvolutionPackPlanRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.federation.stage(
            archive_name,
            plan_sha256=request.plan_sha256,
        )
    except (FileExistsError, FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/quarantine/{pack_id}/reproduce")
async def reproduce_evolution_pack(pack_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return await app.federation.reproduce(pack_id)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/quarantine/{pack_id}/install")
async def install_evolution_pack(pack_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return await app.federation.install(pack_id)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/federation/library/synthesis")
async def federation_synthesis(app: AppDependency) -> dict[str, Any]:
    return app.federation.synthesis_status()


@router.get("/v1/federation/registry/status")
async def federation_registry_status(app: AppDependency) -> dict[str, Any]:
    return app.federation.capability_registry.status()


@router.post("/v1/federation/registry/candidates/{archive_name}", status_code=202)
async def submit_federation_candidate(
    archive_name: str,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.federation.submit_registry_candidate(archive_name)
    except (FileNotFoundError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/federation/registry/candidates/{pack_id}")
async def federation_candidate(pack_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.federation.capability_registry.candidate(pack_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/v1/federation/registry/candidates/{pack_id}/reviews")
async def review_federation_candidate(
    pack_id: str,
    request: FederationCommunityReviewRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.federation.review_registry_candidate(
            pack_id,
            **request.model_dump(),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/registry/candidates/{pack_id}/admit")
async def admit_federation_candidate(pack_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.federation.admit_registry_candidate(pack_id)
    except (FileNotFoundError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/registry/packs/{pack_id}/revoke")
async def revoke_federation_pack(
    pack_id: str,
    request: FederationRevocationRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.federation.revoke_registry_pack(pack_id, reason=request.reason)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/federation/registry/publish")
async def publish_federation_registry(app: AppDependency) -> dict[str, Any]:
    try:
        return await app.federation.publish_registry()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

