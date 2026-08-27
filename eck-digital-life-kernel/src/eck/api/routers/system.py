from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query

from eck import __version__
from eck.api.dependencies import AppDependency
from eck.core.time import utc_now
from eck.domain.enums import KernelPhase, TaskStatus
from eck.runtime.shutdown import request_shutdown

router = APIRouter()


@router.get("/health")
async def health(app: AppDependency) -> dict[str, object]:
    brain, coder = await asyncio.gather(app.brain.health(), app.coder_brain.health())
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
        "coder": coder.model_dump(mode="json"),
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

@router.get("/v1/kernel/status")
async def kernel_status(app: AppDependency) -> Any:
    return app.kernel.status()

@router.post("/v1/kernel/start")
async def kernel_start(app: AppDependency) -> Any:
    await app.kernel.start()
    return app.kernel.status()

@router.post("/v1/kernel/pause")
async def kernel_pause(app: AppDependency) -> Any:
    await app.kernel.pause()
    return app.kernel.status()

@router.post("/v1/kernel/resume")
async def kernel_resume(app: AppDependency) -> Any:
    await app.kernel.resume()
    return app.kernel.status()

@router.post("/v1/kernel/sleep")
async def kernel_sleep(app: AppDependency) -> dict[str, Any]:
    run = await app.kernel.request_sleep()
    return {"accepted": True, "run": run}


@router.get("/v1/kernel/sleep/status")
async def kernel_sleep_status(app: AppDependency) -> dict[str, Any]:
    return {"run": app.store.latest_sleep_run()}

@router.get("/v1/system/services")
async def local_service_status(app: AppDependency) -> dict[str, Any]:
    return {
        **app.local_services.status(),
        "forge": app.image_generation.status(),
    }

@router.get("/v1/system/rag")
async def rag_status(app: AppDependency) -> dict[str, Any]:
    return app.rag.status()

@router.post("/v1/system/shutdown", status_code=202)
async def shutdown_system(
    background_tasks: BackgroundTasks,
    app: AppDependency,
) -> dict[str, bool]:
    await app.kernel.pause()
    background_tasks.add_task(request_shutdown)
    return {"accepted": True}

@router.get("/v1/capabilities")
async def capabilities(app: AppDependency) -> dict[str, Any]:
    return {"items": app.registry.list()}

@router.get("/v1/image/status")
async def image_generation_status(app: AppDependency) -> dict[str, Any]:
    generation = app.image_generation.status()
    return {
        **generation,
        "generation": generation,
        "background_removal": app.image_background_removal.status(),
    }

@router.get("/v1/video/status")
async def video_generation_status(app: AppDependency) -> dict[str, Any]:
    return app.video_generation.status()

@router.get("/v1/system/resources")
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

@router.get("/v1/roadmap")
async def roadmap(app: AppDependency) -> dict[str, Any]:
    verified_capabilities = [item["name"] for item in app.registry.list()]
    soul = app.identity_service.status()
    self_model = app.self_model.status()
    core_lab = app.core_lab.status()
    evolution_transactions = app.evolution_transactions.status()
    evolution_director = app.evolution_director.status()
    status_calls = (
        app.skill_bridge.status(), app.project_lab.status(), app.coder_brain.health()
    )
    skill_bridge, project_lab, coder = await asyncio.gather(*status_calls)
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
            "ECK v0.1.0 已完成基礎核心、P0～P5 工程整修與審核式演化交易，具生命週期、"
            "工具、記憶、驗證、監督、資源保護、客觀診斷與可回滾核心更新架構；"
            "Qwen 權重尚未自我訓練，目前不是已證實的 AGI，也沒有證據顯示已超越人類知識水平。"
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
            "reviewed_evolution": evolution_transactions,
            "autonomous_evolution_director": evolution_director,
            "coder_model": coder.model,
            "coder_ready": coder.available,
            "autonomous_projects": project_lab["project_count"],
            "published_projects": project_lab["published_count"],
            "github_ready": project_lab["github"]["ready"],
            "learning_portfolio": app.autonomous_learning.portfolio(),
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
                "version": "P5",
                "title": "可驗證遞迴自我進化",
                "state": "verified",
                "evidence": (
                    "程式專用模型路由、自我影響圖譜、50/30/15/5 自主課程、Skill "
                    "Canary，以及研究驅動的隔離專案與 GitHub 發布閘門已接入。"
                ),
            },
            {
                "version": "Evolution Transaction v1",
                "title": "審核式核心候選演化",
                "state": "verified",
                "evidence": (
                    "隔離候選、固定閘門、外部隱藏評估、精確 Git tree 核准、"
                    "重啟吸收收據與 Windows 啟動失敗回滾已接入；核心不會熱修改。"
                ),
            },
            {
                "version": "P8",
                "title": "證據驅動自治演化導引",
                "state": "verified",
                "evidence": (
                    "只從去重後的重複真實失敗建立改善機會；必須先綁定獨立隱藏評估包，"
                    "才允許隔離起草候選，結構更新仍須人工核准。"
                ),
            },
            {
                "version": "Next",
                "title": "跨平台失敗回滾與真實任務陰影重播",
                "state": "not_verified",
                "evidence": (
                    "仍需為 Linux、Docker 與直接 CLI 啟動加入外部 watchdog，"
                    "並擴充真實任務重播集。"
                ),
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
