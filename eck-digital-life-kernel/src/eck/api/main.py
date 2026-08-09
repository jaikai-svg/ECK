import asyncio
import mimetypes
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from eck import __version__
from eck.app import Application, build_application
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import ApprovalStatus, BenchmarkSuite, KernelPhase, TaskStatus
from eck.domain.models import (
    AutonomousActionContext,
    BenchmarkRunCreate,
    ChallengeDraftCreate,
    CoreCandidateRequest,
    LearningThemeCreate,
    MissionCompletionCreate,
    MissionCreate,
    MissionReviewDecision,
    MissionUpdate,
    ObjectiveEvaluationRequest,
    SkillForgeRequest,
    SocialPostObservationCreate,
    TaskCreate,
)
from eck.services.demos import DemoService
from eck.services.dialogue import DialogueService
from eck.services.research import ResearchCurriculumService


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


class ResearchCurriculumRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    cycles: int = Field(default=2, ge=1, le=8)


class CriticalResearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    url: str | None = Field(default=None, min_length=10, max_length=2000)
    timespan: str | None = Field(
        default=None,
        pattern=r"^\d{1,3}(?:min|h|d|w|m)$",
    )


class CognitiveBundleRequest(BaseModel):
    include_artifacts: bool = False


class LearningThemeStateRequest(BaseModel):
    active: bool


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
            await application.image_generation.close()
            await application.image_background_removal.close()

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

    def get_application() -> Application:
        return cast(Application, api.state.application)

    AppDependency = Annotated[Application, Depends(get_application)]

    @api.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @api.get("/health")
    async def health(app: AppDependency) -> dict[str, object]:
        brain = await app.brain.health()
        chain_valid, failed_sequence = app.store.verify_event_chain_incremental()
        status = app.kernel.status()
        latest_admitted = app.store.latest_experience(admitted=True)
        learning_origin = latest_admitted.created_at if latest_admitted else status.started_at
        minutes_since_learning = (
            max(0.0, (utc_now() - learning_origin).total_seconds() / 60)
            if learning_origin
            else None
        )
        active_tasks = app.store.list_tasks(
            statuses=(TaskStatus.QUEUED, TaskStatus.RUNNING), limit=500
        )
        learning_tasks = [item for item in active_tasks if "no-learning" not in item.labels]
        stale_running = [
            item
            for item in learning_tasks
            if item.status is TaskStatus.RUNNING
            and (utc_now() - item.updated_at).total_seconds()
            >= app.settings.learning_stall_minutes * 60
        ]
        stalled = bool(
            status.phase is KernelPhase.RUNNING
            and minutes_since_learning is not None
            and minutes_since_learning >= app.settings.learning_stall_minutes
        )
        if stale_running:
            learning_detail = (
                f"{stale_running[0].action.capability} 執行超過停滯門檻，應自動復原或重試。"
            )
        elif stalled and not learning_tasks:
            learning_detail = "沒有可執行的學習任務；監督者應建立新的可驗證考驗。"
        elif stalled:
            learning_detail = "已有學習任務，但尚未產生新的驗證准入。"
        elif learning_tasks:
            learning_detail = "學習任務正在佇列或執行中。"
        else:
            learning_detail = "最近一次驗證學習仍在允許間隔內。"
        return {
            "status": "ok" if chain_valid else "degraded",
            "version": __version__,
            "kernel": status.model_dump(mode="json"),
            "brain": brain.model_dump(mode="json"),
            "image_generation": app.image_generation.status(),
            "image_background_removal": app.image_background_removal.status(),
            "video_generation": app.video_generation.status(),
            "event_chain": {
                "valid": chain_valid,
                "failed_sequence": failed_sequence,
            },
            "safety": {
                "network_enabled": app.settings.network_enabled,
                "system_file_mutation_enabled": app.settings.system_file_mutation_enabled,
                "paid_services_enabled": False,
                "public_ai_disclosure_required": True,
            },
            "memory": {
                "experiences": app.store.count_experiences(),
                "admitted_experiences": app.store.count_experiences(admitted=True),
                "knowledge": app.store.count_knowledge(),
                "reflections": app.store.count_reflections(),
                "skills": app.store.count_skills(),
            },
            "critical_research": app.store.research_quality_metrics(
                window=app.settings.critical_research_quality_window,
                max_inconclusive_ratio=(
                    app.settings.critical_research_max_inconclusive_ratio
                ),
            ),
            "learning_progress": {
                "status": "stalled" if stalled else ("working" if learning_tasks else "idle"),
                "stalled": stalled,
                "stall_threshold_minutes": app.settings.learning_stall_minutes,
                "minutes_since_last_admission": (
                    round(minutes_since_learning, 1)
                    if minutes_since_learning is not None
                    else None
                ),
                "last_admitted_at": (
                    latest_admitted.created_at.isoformat() if latest_admitted else None
                ),
                "last_capability": latest_admitted.capability if latest_admitted else None,
                "active_learning_tasks": len(learning_tasks),
                "stale_running_tasks": len(stale_running),
                "detail": learning_detail,
            },
            "goals": {
                "challenges": app.store.count_challenges(),
                "missions": app.store.count_missions(),
                "benchmark_runs": app.store.count_benchmark_runs(),
            },
            "supervisor": app.supervisor.status(),
            "autonomous_learning": app.autonomous_learning.status(),
            "runtime_version": app.versions.status().model_dump(mode="json"),
            "scheduler": {
                "autonomous_learning_percent": app.settings.autonomous_learning_percent,
                "challenge_execution_percent": app.settings.challenge_execution_percent,
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

    @api.get("/v1/image/status")
    async def image_generation_status(app: AppDependency) -> dict[str, Any]:
        generation = app.image_generation.status()
        return {
            **generation,
            "generation": generation,
            "background_removal": app.image_background_removal.status(),
        }

    @api.get("/v1/video/status")
    async def video_generation_status(app: AppDependency) -> dict[str, Any]:
        return app.video_generation.status()

    @api.get("/v1/system/resources")
    async def system_resources(
        app: AppDependency,
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            app.resources.snapshot,
            include_project=True,
            force_project=refresh,
        )
        result["workloads"] = {
            "image_generation": app.image_generation.status(),
            "video_generation": app.video_generation.status(),
        }
        result["notes"] = {
            "project_size": (
                "可讀檔案的邏輯總量；硬連結、稀疏檔案與無權限路徑可能使實際磁碟占用不同。"
            ),
            "disk_active_time": (
                "此頁顯示容量而非工作管理員的 SSD 活動時間；"
                "模型載入、CPU offload 與換頁會造成活動時間尖峰。"
            ),
        }
        return result

    @api.get("/v1/roadmap")
    async def roadmap(app: AppDependency) -> dict[str, Any]:
        verified_capabilities = [item["name"] for item in app.registry.list()]
        soul = app.identity_service.status()
        self_model = app.self_model.status()
        skill_bridge = await app.skill_bridge.status()
        core_lab = app.core_lab.status()
        runtime_skills = [
            item
            for item in app.store.list_runtime_skills(limit=1000)
            if item.status.value == "active"
        ]
        return {
            "classification": "long_term_target",
            "mission": (
                "建立一個可在本機長期運行、主動發現未知、取得可靠來源、規劃並驗證行動，"
                "同時理解主機資源邊界、不以耗盡電腦為成長代價，持續累積可移植知識與技能，"
                "最終以高標準數位能力服務使用者並造福人類的自主學習核心。"
            ),
            "current_truth": (
                "ECK v0.1.0 已完成基礎核心與 P0～P3 工程整修，具生命週期、工具、記憶、"
                "驗證、監督、資源保護與客觀診斷架構；Qwen 權重尚未自我訓練，目前不是已證實的 AGI，"
                "也沒有證據顯示已超越人類知識水平。"
            ),
            "verified_now": {
                "registered_capabilities": verified_capabilities,
                "verified_experiences": app.store.count_experiences(admitted=True),
                "active_runtime_skills": len(runtime_skills),
                "local_image_stack": app.image_generation.status(),
                "background_removal": app.image_background_removal.status(),
                "local_video_stack": app.video_generation.status(),
                "resource_protection": app.resources.quick_snapshot()["pressure"],
                "event_chain_valid": app.store.verify_event_chain_incremental()[0],
                "soul_integrity": soul["integrity_valid"],
                "soul_revision": soul["revision"],
                "repository_self_model": bool(self_model.get("initialized")),
                "research_skill_conversion": skill_bridge["conversion_verified"],
                "active_generated_skills": skill_bridge["active_generated_skills"],
                "core_candidate_count": core_lab["candidate_count"],
                "live_core_mutation": core_lab["live_core_mutation"],
            },
            "milestones": [
                {
                    "version": "v0.1.0",
                    "title": "可驗證數位生命核心",
                    "state": "verified",
                    "evidence": "生命週期、事件鏈、成功契約、記憶與能力註冊已有自動驗收。",
                },
                {
                    "version": "P0",
                    "title": "可靠性整修",
                    "state": "verified",
                    "evidence": "任務去重、逾時、重試與中斷恢復已納入測試。",
                },
                {
                    "version": "P1",
                    "title": "指令與媒體可靠性",
                    "state": "verified",
                    "evidence": "斜線指令、圖像品質契約與 CogVideoX 本機煙霧測試已通過。",
                },
                {
                    "version": "P2",
                    "title": "資源感知長期運行",
                    "state": "verified",
                    "evidence": "主機資源監控、工作區容量快取、背景節流與模型閒置釋放已接入。",
                },
                {
                    "version": "P3",
                    "title": "客觀能力評估",
                    "state": "verified",
                    "evidence": "固定本機診斷、模型雜湊、重現率、同條件比較與學習產率稽核已接入。",
                },
                {
                    "version": "P4",
                    "title": "可稽核自我認知與隔離演化",
                    "state": "verified",
                    "evidence": (
                        "SOUL 身分、雜湊程式庫自我模型、研究轉技能閘門、隔離核心候選與"
                        "固定回歸測試已接入；結構更新仍須人工核准。"
                    ),
                },
                {
                    "version": "Next",
                    "title": "安全核心候選自我改進",
                    "state": "not_verified",
                    "evidence": "需建立隔離 worktree、保留真實任務、陰影重播與人工批准切換。",
                },
            ],
            "targets": [
                {
                    "title": "持續生命週期",
                    "state": "in_progress",
                    "measure": "長期不中斷運行、工作程序可獨立重啟與熱切換。",
                },
                {
                    "title": "自主認知與未知偵測",
                    "state": "in_progress",
                    "measure": "能標示不確定性、提出問題並選擇本機模型、網路來源或工具查證。",
                },
                {
                    "title": "持續技能成長",
                    "state": "in_progress",
                    "measure": "新技能必須通過隔離測試、證據驗證與回歸檢查後才能啟用。",
                },
                {
                    "title": "資源可觀測與自我節流",
                    "state": "verified",
                    "measure": "顯示主機與專案用量；資源臨界時暫緩背景工作，保留緊急人類任務。",
                },
                {
                    "title": "複雜任務自治",
                    "state": "not_verified",
                    "measure": "在合法、安全與零付費邊界內規劃、執行、修正並交付真實成果。",
                },
                {
                    "title": "能力可量化增強",
                    "state": "not_verified",
                    "measure": "以固定基準、真實任務、消融測試與人工盲評證明能力提升。",
                },
                {
                    "title": "通用或超人能力",
                    "state": "aspirational",
                    "measure": "只有跨領域、可重現且由外部專家驗證的證據才能支持此宣稱。",
                },
                {
                    "title": "經驗移植與人類福祉",
                    "state": "not_verified",
                    "measure": "將可追溯技能、知識、失敗結果與安全界線移植到下一個模型。",
                },
            ],
            "claim_policy": (
                "目標不等於能力；執行時間不等於變聰明。只有固定評測改善、真實任務成果與"
                "可重現外部證據，才會顯示為已驗證進展。"
            ),
        }

    @api.get("/v1/supervisor/status")
    async def supervisor_status(app: AppDependency) -> dict[str, Any]:
        return app.supervisor.status()

    @api.get("/v1/learning/autonomous/status")
    async def autonomous_learning_status(app: AppDependency) -> dict[str, Any]:
        return app.autonomous_learning.status()

    @api.get("/v1/learning/community-sources")
    async def community_learning_sources(app: AppDependency) -> dict[str, Any]:
        return app.community_sources.status()

    @api.get("/v1/learning/themes")
    async def list_learning_themes(app: AppDependency) -> dict[str, Any]:
        return {
            "items": app.store.list_learning_themes(limit=100),
            "eck_focus_percent": app.settings.autonomous_eck_focus_percent,
            "theme_focus_percent": 100 - app.settings.autonomous_eck_focus_percent,
        }

    @api.post("/v1/learning/themes", status_code=201)
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

    @api.patch("/v1/learning/themes/{theme_id}")
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

    @api.delete("/v1/learning/themes/{theme_id}", status_code=204)
    async def delete_learning_theme(theme_id: str, app: AppDependency) -> None:
        try:
            app.store.delete_learning_theme(theme_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await app.events.publish("LearningThemeDeleted", theme_id, {})

    @api.get("/v1/learning/skill-tree")
    async def learning_skill_tree(app: AppDependency) -> dict[str, Any]:
        return app.skill_graph.build()

    @api.get("/v1/learning/skill-tree/search")
    async def search_learning_skill_tree(
        app: AppDependency,
        query: str = Query(alias="q", min_length=2, max_length=200),
        limit: int = Query(default=8, ge=1, le=30),
    ) -> dict[str, Any]:
        return {"items": app.skill_graph.search(query, limit=limit)}

    @api.get("/v1/evolution/status")
    async def evolution_status(app: AppDependency) -> dict[str, Any]:
        return await app.evolution.status()

    @api.get("/v1/identity/soul")
    async def identity_soul(app: AppDependency) -> dict[str, Any]:
        return app.identity_service.status()

    @api.get("/v1/self-model")
    async def repository_self_model(app: AppDependency) -> dict[str, Any]:
        return app.self_model.status()

    @api.post("/v1/self-model/refresh")
    async def refresh_repository_self_model(app: AppDependency) -> dict[str, Any]:
        return app.self_model.refresh()

    @api.get("/v1/evolution/skill-bridge")
    async def research_skill_bridge_status(app: AppDependency) -> dict[str, Any]:
        return await app.skill_bridge.status()

    @api.post("/v1/evolution/skill-bridge/run", status_code=202)
    async def run_research_skill_bridge(app: AppDependency) -> dict[str, Any]:
        try:
            return await app.skill_bridge.run_if_needed(force=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/v1/evolution/core-candidates")
    async def list_core_candidates(app: AppDependency) -> dict[str, Any]:
        return {"status": app.core_lab.status(), "items": app.core_lab.list_candidates()}

    @api.get("/v1/evolution/core-candidates/{candidate_id}")
    async def get_core_candidate(candidate_id: str, app: AppDependency) -> dict[str, Any]:
        try:
            return app.core_lab.get_candidate(candidate_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/v1/evolution/core-candidates", status_code=201)
    async def create_core_candidate(
        request: CoreCandidateRequest,
        app: AppDependency,
    ) -> dict[str, Any]:
        try:
            return await app.core_lab.create_candidate(request)
        except (FileNotFoundError, OSError, RuntimeError, SyntaxError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/v1/evolution/core-candidates/{candidate_id}/validate")
    async def validate_core_candidate(
        candidate_id: str,
        app: AppDependency,
    ) -> dict[str, Any]:
        try:
            return await app.core_lab.validate_candidate(candidate_id)
        except (FileNotFoundError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/v1/portability/bundles", status_code=201)
    async def export_cognitive_bundle(
        request: CognitiveBundleRequest,
        app: AppDependency,
    ) -> dict[str, Any]:
        return await app.portability.export(include_artifacts=request.include_artifacts)

    @api.get("/v1/portability/bundles/{archive_name}")
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

    @api.get("/v1/portability/bundles/{archive_name}/verify")
    async def verify_cognitive_bundle(
        archive_name: str,
        app: AppDependency,
    ) -> dict[str, Any]:
        try:
            return app.portability.verify(archive_name)
        except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/v1/runtime/status")
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

    @api.post("/v1/runtime/worker/build")
    async def build_skill_worker(app: AppDependency) -> dict[str, Any]:
        return await app.forge.build_worker()

    @api.get("/v1/runtime/skills")
    async def list_runtime_skills(app: AppDependency) -> dict[str, Any]:
        return {"items": app.store.list_runtime_skills(limit=500)}

    @api.post("/v1/runtime/skills/forge", status_code=202)
    async def forge_runtime_skill(request: SkillForgeRequest, app: AppDependency) -> Any:
        try:
            return await app.forge.forge(request)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/v1/runtime/skills/validate")
    async def validate_runtime_skills(app: AppDependency) -> dict[str, Any]:
        return {"items": await app.forge.validate_pending()}

    @api.post("/v1/runtime/skills/{runtime_skill_id}/validate")
    async def validate_runtime_skill(runtime_skill_id: str, app: AppDependency) -> Any:
        try:
            return await app.forge.validate_skill(runtime_skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/v1/chat/commands")
    async def chat_commands() -> dict[str, Any]:
        return {"items": DialogueService.command_catalog()}

    @api.post("/v1/chat")
    async def chat(request: ChatRequest, app: AppDependency) -> dict[str, Any]:
        try:
            return await DialogueService(app).respond(
                request.message,
                [item.model_dump() for item in request.history],
            )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.post("/v1/research/curricula", status_code=202)
    async def start_research_curriculum(
        request: ResearchCurriculumRequest,
        app: AppDependency,
    ) -> dict[str, object]:
        cycles = min(request.cycles, app.settings.academic_research_max_cycles)
        return await ResearchCurriculumService(app).submit(request.topic, cycles)

    @api.post("/v1/research/relevance-audit")
    async def audit_research_relevance(app: AppDependency) -> dict[str, object]:
        return await ResearchCurriculumService(app).audit_relevance()

    @api.post("/v1/research/critical", status_code=202)
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

    @api.get("/v1/research/runs")
    async def list_critical_research_runs(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_research_runs(limit=limit)}

    @api.get("/v1/research/runs/{run_id}")
    async def get_critical_research_run(run_id: str, app: AppDependency) -> Any:
        try:
            return app.store.get_research_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/v1/research/quality")
    async def get_critical_research_quality(app: AppDependency) -> dict[str, Any]:
        return app.store.research_quality_metrics(
            window=app.settings.critical_research_quality_window,
            max_inconclusive_ratio=app.settings.critical_research_max_inconclusive_ratio,
        )

    @api.post("/v1/challenges/social-engagement", status_code=202)
    async def bootstrap_social_challenge(app: AppDependency) -> Any:
        return await app.challenges.bootstrap_social_engagement()

    @api.get("/v1/challenges")
    async def list_challenges(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_challenges(limit=limit)}

    @api.get("/v1/challenges/drafts")
    async def list_challenge_drafts(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_challenge_drafts(limit=limit)}

    @api.post("/v1/challenges/drafts", status_code=201)
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

    @api.get("/v1/missions")
    async def list_missions(
        app: AppDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"items": app.store.list_missions(limit=limit)}

    @api.post("/v1/missions", status_code=201)
    async def create_mission(request: MissionCreate, app: AppDependency) -> Any:
        return await app.missions.create(request)

    @api.patch("/v1/missions/{mission_id}")
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

    @api.post("/v1/missions/{mission_id}/completion")
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

    @api.post("/v1/missions/{mission_id}/review")
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

    @api.post("/v1/missions/{mission_id}/reopen")
    async def reopen_mission(mission_id: str, app: AppDependency) -> Any:
        try:
            return await app.missions.reopen(mission_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.delete("/v1/missions/{mission_id}")
    async def cancel_mission(mission_id: str, app: AppDependency) -> Any:
        try:
            return await app.missions.cancel(mission_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/v1/challenges/{challenge_id}")
    async def get_challenge(challenge_id: str, app: AppDependency) -> dict[str, Any]:
        try:
            challenge = app.store.get_challenge(challenge_id)
            observations = app.store.list_social_post_observations(challenge_id, limit=100)
            return {"challenge": challenge, "observations": observations}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/v1/challenges/{challenge_id}/plan")
    async def replan_challenge(challenge_id: str, app: AppDependency) -> Any:
        try:
            return await app.challenges.plan(challenge_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/v1/challenges/{challenge_id}/social-observations", status_code=201)
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

    @api.get("/v1/evaluations")
    async def evaluation_dashboard(app: AppDependency) -> dict[str, Any]:
        return app.evaluations.dashboard()

    @api.post("/v1/evaluations/runs", status_code=201)
    async def record_evaluation(
        request: BenchmarkRunCreate,
        app: AppDependency,
    ) -> Any:
        try:
            return await app.evaluations.record(request)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/v1/evaluations/objective", status_code=201)
    async def run_objective_evaluation(
        request: ObjectiveEvaluationRequest,
        app: AppDependency,
    ) -> dict[str, Any]:
        try:
            return await app.evaluations.run_objective(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.get("/v1/evaluations/compare")
    async def compare_evaluations(
        app: AppDependency,
        suite: Annotated[BenchmarkSuite, Query()] = BenchmarkSuite.ECK_P3_OBJECTIVE,
    ) -> dict[str, Any]:
        return app.evaluations.compare(suite)

    @api.post("/v1/governance/autonomous-actions/evaluate")
    async def evaluate_autonomous_action(
        request: AutonomousActionContext,
        app: AppDependency,
    ) -> Any:
        return app.autonomy.evaluate(request)

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
