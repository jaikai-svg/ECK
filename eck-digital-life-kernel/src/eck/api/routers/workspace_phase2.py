from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from eck.api.dependencies import AppDependency
from eck.modules.archive.service import ArchiveIntegrityError, ArchiveOfflineError
from eck.modules.library.authoring import LibraryReadinessError

router = APIRouter(prefix="/v1/workspace", tags=["workspace-phase2"])


class ArchiveRequest(BaseModel):
    remove_local: bool | None = None


class DomainCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=4000)
    knowledge_selector: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] | None = None


class RelationCreateRequest(BaseModel):
    source_knowledge_id: str
    target_knowledge_id: str
    relation_type: str
    rationale: str = Field(default="", max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    verified: bool = False


class AuthorRequest(BaseModel):
    reason: str = Field(default="Readiness gate passed", min_length=3, max_length=1000)


class SuggestionRequest(BaseModel):
    revision_id: str | None = None
    suggestion_type: Literal["question", "revision", "chapter", "reverify"] = "revision"
    content: str = Field(min_length=3, max_length=8000)


@router.get("/results")
def results(
    app: AppDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    artifact_type: Annotated[str, Query(max_length=40)] = "",
    status: Annotated[str, Query(max_length=40)] = "",
    project_id: Annotated[str, Query(max_length=200)] = "",
    skill_id: Annotated[str, Query(max_length=200)] = "",
    q: Annotated[str, Query(max_length=200)] = "",
    created_from: Annotated[str, Query(max_length=40)] = "",
    created_to: Annotated[str, Query(max_length=40)] = "",
) -> dict[str, Any]:
    return app.artifacts.page(
        limit=limit,
        offset=offset,
        artifact_type=artifact_type,
        status=status,
        project_id=project_id,
        skill_id=skill_id,
        query=q,
        created_from=created_from,
        created_to=created_to,
    )


@router.get("/results/{artifact_id}")
def result_detail(artifact_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.artifacts.detail(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/results/{artifact_id}/preview")
def result_preview(
    artifact_id: str,
    app: AppDependency,
    background_tasks: BackgroundTasks,
) -> FileResponse:
    try:
        artifact = app.store.get_artifact(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(str(artifact["local_path"]))
    cached = False
    if not path.exists():
        try:
            path = app.archive.acquire(artifact_id)
            cached = True
        except ArchiveOfflineError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ArchiveIntegrityError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not path.is_file():
        if cached:
            app.archive.release(artifact_id)
        raise HTTPException(
            status_code=400,
            detail="Directory artifacts require download packaging.",
        )
    if cached:
        background_tasks.add_task(app.archive.release, artifact_id)
    return FileResponse(path, media_type=str(artifact["mime_type"]), filename=path.name)


@router.post("/results/{artifact_id}/archive")
def archive_result(
    artifact_id: str,
    request: ArchiveRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.archive.archive(artifact_id, remove_local=request.remove_local)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArchiveOfflineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ArchiveIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/results/{artifact_id}/restore")
def restore_result(artifact_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        path = app.archive.acquire(artifact_id)
        app.archive.release(artifact_id)
        return {"artifact_id": artifact_id, "cache_path": str(path), "verified": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArchiveOfflineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ArchiveIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/archive/status")
def archive_status(app: AppDependency) -> dict[str, Any]:
    return app.archive.status()


@router.get("/library/domains")
def library_domains(app: AppDependency) -> dict[str, Any]:
    return app.library_authoring.domains()


@router.post("/library/domains")
def create_library_domain(
    request: DomainCreateRequest, app: AppDependency
) -> dict[str, Any]:
    try:
        return app.library_authoring.create_domain(
            title=request.title,
            description=request.description,
            knowledge_selector=request.knowledge_selector,
            thresholds=request.thresholds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/library/domains/{domain_id}")
def library_domain(domain_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.library_authoring.domain(domain_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/library/domains/{domain_id}/relations")
def create_library_relation(
    domain_id: str,
    request: RelationCreateRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        app.store.get_library_domain(domain_id)
        return app.library_authoring.add_relation(**request.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/library/domains/{domain_id}/evaluate")
def evaluate_library_domain(domain_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.library_authoring.evaluate(domain_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/library/domains/{domain_id}/author")
def author_library_domain(
    domain_id: str,
    request: AuthorRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return app.library_authoring.author(domain_id, reason=request.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LibraryReadinessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/library/books/{book_id}")
def library_book(book_id: str, app: AppDependency) -> dict[str, Any]:
    try:
        return app.library_authoring.book(book_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/library/books/{book_id}/revisions/{revision_id}/download")
def download_library_revision(
    book_id: str,
    revision_id: str,
    app: AppDependency,
    format: Literal["markdown", "json"] = "markdown",
) -> FileResponse:
    try:
        revision = app.store.get_book_revision(revision_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if revision["book_id"] != book_id:
        raise HTTPException(status_code=404, detail="Revision does not belong to this book.")
    key = "markdown_path" if format == "markdown" else "manifest_path"
    path = Path(str(revision[key])).resolve()
    try:
        path.relative_to(app.settings.library_books_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Unsafe Library path.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Library revision file is unavailable.")
    return FileResponse(path, filename=path.name)


@router.post("/library/books/{book_id}/suggestions")
async def create_library_suggestion(
    book_id: str,
    request: SuggestionRequest,
    app: AppDependency,
) -> dict[str, Any]:
    try:
        return await app.library_authoring.suggest(
            book_id=book_id,
            revision_id=request.revision_id,
            suggestion_type=request.suggestion_type,
            content=request.content,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
