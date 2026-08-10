from __future__ import annotations

import json
from pathlib import Path

import pytest

from eck.brain.base import BrainResponse
from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.domain.models import MissionCreate, MissionReviewDecision, MissionStepDefinition
from eck.services.dialogue import DialogueService

EXPECTED_WEBSITE_STEPS = [
    "workspace.prepare",
    "reference.research",
    "software.specify",
    "architecture.design",
    "architecture.plan",
    "software.implement",
    "software.microtask.1",
    "software.microtask.2",
    "software.microtask.3",
    "software.microtask.4",
    "software.microtask.5",
    "software.microtask.6",
    "software.enhance",
    "quality.review.1",
    "quality.improve.1",
    "quality.review.2",
    "quality.improve.2",
    "quality.review.3",
    "quality.improve.3",
    "software.validate",
    "learning.distill",
    "artifact.package",
    "github.publish",
    "mission.submit",
]


async def run_steps(application, count: int):
    completed = []
    for _ in range(count):
        step = await application.mission_executor.run_next()
        assert step is not None
        completed.append(step)
    return completed


async def run_mission(application):
    completed = []
    for _ in range(50):
        if not application.mission_executor.has_runnable_work():
            break
        step = await application.mission_executor.run_next()
        assert step is not None
        completed.append(step)
    else:
        pytest.fail("Mission executor did not reach a durable terminal state.")
    return completed


@pytest.mark.asyncio
async def test_dialogue_compiles_website_request_into_durable_mission(application) -> None:
    result = await DialogueService(application).respond(
        "製作一個簡單的旅遊網站並向我展示成果",
        [],
    )

    mission_id = str(result["mission_id"])
    mission = application.store.get_mission(mission_id)
    steps = application.store.list_mission_steps(mission_id)

    assert result["tool"] == "mission.execute"
    assert result["pending"] is True
    assert mission.progress["execution_kind"] == "software_project"
    assert [step.step_key for step in steps] == EXPECTED_WEBSITE_STEPS
    assert mission.progress["executor"] == "p6-durable-react.v2"
    assert mission.progress["step_count"] == 24


@pytest.mark.asyncio
async def test_durable_executor_delivers_verified_site_and_review_evidence(application) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="建立旅遊網站",
            objective="製作一個簡單的旅遊網站並向我展示成果",
            completion_requirements="可預覽、可下載、通過本機驗證並等待人工驗收",
            priority="urgent",
            execution_kind="software_project",
        )
    )

    completed = await run_mission(application)

    final = application.store.get_mission(mission.mission_id)
    cycles = application.store.list_mission_react_cycles(mission.mission_id)
    preview = application.mission_executor.preview_path(mission.mission_id)
    package = application.mission_executor.package_path(mission.mission_id)

    assert all(step.status is MissionStepStatus.SUCCEEDED for step in completed)
    assert final.status is MissionStatus.AWAITING_REVIEW
    assert final.result_summary.startswith("P6 已完成")
    assert any(item.endswith("/preview/") for item in final.evidence)
    assert any(item.startswith("sha256:") for item in final.evidence)
    assert preview.name == "index.html" and "旅遊" in preview.read_text(encoding="utf-8")
    assert package.suffix == ".zip" and package.stat().st_size > 0
    assert len(completed) == 24
    assert len(cycles) == 24
    assert all(cycle.reason_summary for cycle in cycles)
    assert all(cycle.status is MissionCycleStatus.SUCCEEDED for cycle in cycles)
    review_steps = [step for step in completed if step.action_kind == "quality.review"]
    microtasks = [step for step in completed if step.action_kind == "software.microtask"]
    assert len(microtasks) == 6
    assert all(step.output["changed"] for step in microtasks)
    assert len(review_steps) == 3
    assert [step.inputs["round"] for step in review_steps] == [1, 2, 3]
    assert all(len(step.output["findings"]) >= 5 for step in review_steps)
    assert final.progress["learning_pattern"]["activation_policy"].startswith("Reusable only")
    assert application.mission_executor._project_name(final) == "travel-task-0001"
    status = application.mission_executor.status(mission.mission_id)
    assert status["items"][0]["workspace_bytes"] > 0
    assert status["storage"]["used_bytes"] > 0


@pytest.mark.asyncio
async def test_python_project_worker_uses_isolated_validation_contract(
    application,
    monkeypatch,
) -> None:
    async def successful_validation(source_dir, *, objective):
        assert source_dir.joinpath("mission_app.py").is_file()
        assert "API" in objective
        return {
            "success": True,
            "checks": ["static-quality", "docker-pytest"],
            "isolated": True,
            "network": "none",
        }

    monkeypatch.setattr(
        application.project_lab,
        "validate_python_directory",
        successful_validation,
    )
    mission = await application.missions.create(
        MissionCreate(
            title="建立高併發 API 專案",
            objective="建立一個具備防禦性設計的高併發 API",
            completion_requirements="完整來源、測試、隔離驗證與可下載封裝",
            execution_kind="software_project",
        )
    )

    completed = await run_mission(application)

    final = application.store.get_mission(mission.mission_id)
    source = application.settings.mission_workspace_dir / mission.mission_id / "source"

    assert final.status is MissionStatus.AWAITING_REVIEW
    assert source.joinpath("mission_app.py").is_file()
    assert source.joinpath("tests", "test_mission_app.py").is_file()
    assert not any(item.endswith("/preview/") for item in final.evidence)
    assert len(completed) == 24


@pytest.mark.asyncio
async def test_failed_observation_triggers_correction_and_fixed_contract_replay(
    application,
    monkeypatch,
) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="建立需要修正的網站",
            objective="建立一個旅遊網站並在驗證失敗後修正",
            completion_requirements="必須保留失敗觀察並重測相同契約",
            execution_kind="software_project",
        )
    )
    completed = await run_steps(application, 19)
    assert all(step.status is MissionStepStatus.SUCCEEDED for step in completed)

    original_validator = application.mission_executor._validate_site
    calls = 0

    def fail_once(source_dir, current_mission, *, enforce_threshold=True):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": False,
                "issues": ["simulated deterministic check failure"],
                "checks": [],
            }
        return original_validator(
            source_dir,
            current_mission,
            enforce_threshold=enforce_threshold,
        )

    monkeypatch.setattr(application.mission_executor, "_validate_site", fail_once)
    failed = await application.mission_executor.run_next()
    corrected = await application.mission_executor.run_next()

    assert failed is not None and failed.status is MissionStepStatus.PENDING
    assert corrected is not None and corrected.status is MissionStepStatus.SUCCEEDED
    assert corrected.attempts == 2
    cycles = application.store.list_mission_react_cycles(mission.mission_id)
    assert any(cycle.status is MissionCycleStatus.NEEDS_CORRECTION for cycle in cycles)
    assert any("重跑" in cycle.correction for cycle in cycles)


@pytest.mark.asyncio
async def test_running_step_and_react_cycle_recover_after_restart(application) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="建立可恢復網站",
            objective="建立一個可在核心重啟後續跑的網站",
            completion_requirements="重啟後不得遺失步驟與觀察",
            execution_kind="software_project",
        )
    )
    step = application.store.claim_next_mission_step()
    assert step is not None
    cycle = application.store.create_mission_react_cycle(
        step,
        reason_summary="先建立隔離工作區，再依工具結果前進。",
        action={"tool": step.action_kind},
    )

    recovered = application.store.recover_running_mission_steps()
    recovered_step = application.store.get_mission_step(step.step_id)
    recovered_cycle = application.store.get_mission_react_cycle(cycle.cycle_id)

    assert mission.mission_id == recovered_step.mission_id
    assert recovered == 1
    assert recovered_step.status is MissionStepStatus.PENDING
    assert recovered_cycle.status is MissionCycleStatus.NEEDS_CORRECTION
    assert "restart" in recovered_cycle.correction.casefold()


def test_mission_preview_cannot_escape_isolated_workspace(application) -> None:
    with pytest.raises((KeyError, ValueError)):
        application.mission_executor.preview_path("mission_" + "0" * 32, "../eck.db")


@pytest.mark.asyncio
async def test_structured_coder_reason_spec_and_files_are_used(application, monkeypatch) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="建立設計旅遊網站",
            objective="建立一個有完整設計的旅遊網站",
            completion_requirements="完整來源與可重現驗證",
            execution_kind="software_project",
        )
    )
    generated_files = application.mission_executor._fallback_site_files(mission)

    async def structured_chat(messages, *, format_schema=None, options=None):
        del format_schema, options
        system = messages[0]["content"]
        if "持久化任務控制器" in system:
            payload = {
                "reason_summary": "先辨識未知條件，再執行單一工具。",
                "unknowns": ["目前檔案狀態"],
                "tool": "registered-action",
                "success_check": "固定驗證器通過",
            }
        elif "產品與前端架構師" in system:
            payload = {
                "project_name": "designed-travel-site",
                "project_type": "static_website",
                "audience": "自由行旅客",
                "pages": ["index.html"],
                "features": ["responsive", "planner"],
                "acceptance_checks": ["local assets", "semantic html"],
            }
        elif "世界級產品設計工程師與前端工程師" in system:
            payload = {"files": generated_files}
        else:
            payload = {"files": generated_files}
        return BrainResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="structured-coder-test",
            raw={},
        )

    monkeypatch.setattr(application.coder_brain, "chat", structured_chat)
    completed = await run_steps(application, 6)
    assert all(step.status is MissionStepStatus.SUCCEEDED for step in completed)

    cycles = application.store.list_mission_react_cycles(mission.mission_id)
    steps = application.store.list_mission_steps(mission.mission_id)
    spec = steps[2].output
    implementation = steps[5].output

    assert "未知項" in cycles[0].reason_summary
    assert spec["project_name"] == "designed-travel-site"
    assert implementation["model"] == "structured-coder-test"


@pytest.mark.asyncio
async def test_site_validator_records_multiple_real_contract_failures(application) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="旅遊壞檔驗證",
            objective="建立旅遊網站",
            completion_requirements="壞檔必須被拒絕",
            execution_kind="software_project",
        )
    )
    await run_steps(application, 6)
    source = application.mission_executor._source_dir(mission.mission_id)
    source.joinpath("index.html").unlink()
    missing_index = application.mission_executor._validate_site(source, mission)
    source.joinpath("index.html").write_text(
        '<html><head><link href="../escape.css"></head><body>TODO</body></html>',
        encoding="utf-8",
    )
    source.joinpath("styles.css").write_text(".x{" + "x" * 500, encoding="utf-8")
    source.joinpath("app.js").write_text("const value = 1;\n" * 20, encoding="utf-8")
    malformed = application.mission_executor._validate_site(source, mission)

    assert missing_index["issues"] == ["index.html is missing"]
    assert malformed["success"] is False
    assert any("title" in issue for issue in malformed["issues"])
    assert any("Semantic" in issue for issue in malformed["issues"])
    assert any("Placeholder" in issue for issue in malformed["issues"])
    assert any("references" in issue for issue in malformed["issues"])


def test_generated_file_contracts_reject_unsafe_or_incomplete_payloads(application) -> None:
    executor = application.mission_executor
    assert executor._validated_site_files(None) == []
    assert executor._validated_python_files(None) == []
    with pytest.raises(ValueError, match="objects"):
        executor._validated_site_files(["bad"])
    with pytest.raises(ValueError, match="Unsafe"):
        executor._validated_site_files([{"path": "../bad.html", "content": "x"}])
    with pytest.raises(ValueError, match="Unsupported"):
        executor._validated_site_files([{"path": "bad.exe", "content": "x"}])
    with pytest.raises(ValueError, match="required files"):
        executor._validated_site_files([{"path": "index.html", "content": "x"}])
    with pytest.raises(ValueError, match="objects"):
        executor._validated_python_files(["bad"])
    with pytest.raises(ValueError, match="Unsupported"):
        executor._validated_python_files([{"path": "bad.exe", "content": "x"}])
    with pytest.raises(ValueError, match="pytest"):
        executor._validated_python_files([{"path": "app.py", "content": "value = 1"}])
    with pytest.raises(ValueError, match="executable source"):
        executor._validated_python_files(
            [{"path": "tests/test_app.py", "content": "def test_ok(): assert 1 == 1"}]
        )


@pytest.mark.asyncio
async def test_terminal_validation_failure_blocks_dependent_steps(application, monkeypatch) -> None:
    application.settings.mission_step_max_attempts = 1
    mission = await application.missions.create(
        MissionCreate(
            title="建立必須阻擋的網站",
            objective="建立一個旅遊網站",
            completion_requirements="驗證失敗不得封裝",
            execution_kind="software_project",
        )
    )
    await run_steps(application, 19)

    monkeypatch.setattr(
        application.mission_executor,
        "_validate_site",
        lambda source, current, *, enforce_threshold=True: {
            "success": False,
            "issues": ["fixed failure"],
            "checks": [],
        },
    )
    failed = await application.mission_executor.run_next()
    final = application.store.get_mission(mission.mission_id)
    steps = application.store.list_mission_steps(mission.mission_id)

    assert failed is not None and failed.status is MissionStepStatus.FAILED
    assert final.status is MissionStatus.BLOCKED
    assert all(
        step.status is MissionStepStatus.BLOCKED
        for step in steps
        if step.sequence > failed.sequence
    )
    assert application.mission_executor.has_runnable_work() is False


@pytest.mark.asyncio
async def test_creator_rejection_replays_all_three_quality_rounds(application) -> None:
    mission = await application.missions.create(
        MissionCreate(
            title="建立互動旅遊網站",
            objective="建立可規劃行程的動態旅遊網站",
            completion_requirements="通過三輪專家審查並等待人工驗收",
            execution_kind="software_project",
        )
    )
    await run_mission(application)

    revised = await application.missions.review(
        mission.mission_id,
        MissionReviewDecision(approved=False, feedback="首頁層次不足，請強化視覺焦點。"),
    )
    steps = application.store.list_mission_steps(mission.mission_id)

    assert revised.status is MissionStatus.ACTIVE
    assert revised.review_feedback == "首頁層次不足，請強化視覺焦點。"
    assert revised.progress["human_revision_round"] == 1
    first_review_sequence = min(
        step.sequence for step in steps if step.action_kind == "quality.review"
    )
    replay = [step for step in steps if step.sequence >= first_review_sequence]
    assert replay
    assert all(step.status is MissionStepStatus.PENDING for step in replay)

    rerun = await run_mission(application)
    final = application.store.get_mission(mission.mission_id)
    assert final.status is MissionStatus.AWAITING_REVIEW
    assert len([step for step in rerun if step.action_kind == "quality.review"]) == 3
    first_review = next(step for step in rerun if step.action_kind == "quality.review")
    assert first_review.output["findings"][0]["evidence"] == revised.review_feedback


def test_only_human_approved_patterns_are_reused(application) -> None:
    first = application.store.create_mission(
        MissionCreate(
            title="旅遊網站設計",
            objective="建立互動旅遊網站",
            completion_requirements="通過驗證",
            execution_kind="software_project",
        )
    )
    pattern = {
        "project_type": "static_website",
        "tags": ["旅遊", "網站"],
        "review_lessons": ["Improve navigation hierarchy."],
    }
    application.store.set_mission_status(
        first.mission_id,
        MissionStatus.APPROVED,
        progress={**first.progress, "learning_pattern": pattern},
    )
    second = application.store.create_mission(
        MissionCreate(
            title="旅遊網站改版",
            objective="建立新的旅遊網站",
            completion_requirements="通過驗證",
            execution_kind="software_project",
        )
    )

    reused = application.mission_executor.council.similar_patterns(
        second,
        project_type="static_website",
    )

    assert reused == [pattern]


def test_dashboard_preserves_active_review_draft() -> None:
    source = Path(__file__).parents[2].joinpath(
        "src", "eck", "dashboard", "app.js"
    ).read_text(encoding="utf-8")

    assert "eck-mission-drafts-v1" in source
    assert "document.activeElement?.closest(\".mission-review-form\")" in source
    assert 'document.addEventListener("input"' in source


def test_legacy_p6_upgrade_adds_architecture_before_review(application) -> None:
    mission = application.store.create_mission(
        MissionCreate(
            title="舊版旅遊網站",
            objective="建立旅遊網站",
            completion_requirements="等待人工驗收",
            execution_kind="software_project",
        )
    )
    application.store.create_mission_steps(
        mission.mission_id,
        (
            MissionStepDefinition(
                step_key="software.implement",
                sequence=30,
                action_kind="software.implement",
                objective="舊版實作",
                inputs={"project_type": "static_website"},
            ),
            MissionStepDefinition(
                step_key="software.validate",
                sequence=40,
                action_kind="software.validate",
                objective="舊版驗證",
                depends_on=("software.implement",),
                inputs={"project_type": "static_website"},
            ),
            MissionStepDefinition(
                step_key="mission.submit",
                sequence=70,
                action_kind="mission.submit",
                objective="舊版提交",
                depends_on=("software.validate",),
                inputs={"project_type": "static_website"},
            ),
        ),
    )
    application.store.set_mission_status(
        mission.mission_id,
        MissionStatus.AWAITING_REVIEW,
        progress={
            "completion_percent": 90,
            "current_step": "依驗收意見改善後重送",
        },
    )

    upgraded = application.mission_executor.upgrade_legacy_graphs()
    refreshed = application.store.get_mission(mission.mission_id)
    steps = application.store.list_mission_steps(mission.mission_id)
    keys = [step.step_key for step in steps]

    assert upgraded == 1
    assert refreshed.status is MissionStatus.ACTIVE
    assert refreshed.progress["executor"] == "p6-durable-react.v2"
    assert refreshed.progress["execution_kind"] == "software_project"
    assert keys.index("reference.research.v2") < keys.index("architecture.design.v2")
    assert keys.index("architecture.design.v2") < keys.index("architecture.plan.v2")
    assert keys.index("architecture.plan.v2") < keys.index("quality.review.1")
    assert len([step for step in steps if step.action_kind == "software.microtask"]) == 6
