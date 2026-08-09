from __future__ import annotations

import json

import pytest

from eck.brain.base import BrainResponse
from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.domain.models import MissionCreate
from eck.services.dialogue import DialogueService


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
    assert [step.step_key for step in steps] == [
        "workspace.prepare",
        "software.specify",
        "software.implement",
        "software.validate",
        "artifact.package",
        "github.publish",
        "mission.submit",
    ]


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

    completed = []
    for _ in range(7):
        step = await application.mission_executor.run_next()
        assert step is not None
        completed.append(step)

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
    assert len(cycles) == 7
    assert all(cycle.reason_summary for cycle in cycles)
    assert all(cycle.status is MissionCycleStatus.SUCCEEDED for cycle in cycles)
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

    for _ in range(7):
        result = await application.mission_executor.run_next()
        assert result is not None and result.status is MissionStepStatus.SUCCEEDED

    final = application.store.get_mission(mission.mission_id)
    source = application.settings.mission_workspace_dir / mission.mission_id / "source"

    assert final.status is MissionStatus.AWAITING_REVIEW
    assert source.joinpath("mission_app.py").is_file()
    assert source.joinpath("tests", "test_mission_app.py").is_file()
    assert not any(item.endswith("/preview/") for item in final.evidence)


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
    for _ in range(3):
        result = await application.mission_executor.run_next()
        assert result is not None and result.status is MissionStepStatus.SUCCEEDED

    original_validator = application.mission_executor._validate_site
    calls = 0

    def fail_once(source_dir, current_mission):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": False,
                "issues": ["simulated deterministic check failure"],
                "checks": [],
            }
        return original_validator(source_dir, current_mission)

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
        elif "世界級前端工程師" in system:
            payload = {"files": generated_files}
        else:
            payload = {"files": generated_files}
        return BrainResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="structured-coder-test",
            raw={},
        )

    monkeypatch.setattr(application.coder_brain, "chat", structured_chat)
    for _ in range(7):
        result = await application.mission_executor.run_next()
        assert result is not None and result.status is MissionStepStatus.SUCCEEDED

    cycles = application.store.list_mission_react_cycles(mission.mission_id)
    spec = application.store.list_mission_steps(mission.mission_id)[1].output
    implementation = application.store.list_mission_steps(mission.mission_id)[2].output

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
    for _ in range(3):
        await application.mission_executor.run_next()
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
    for _ in range(3):
        await application.mission_executor.run_next()

    monkeypatch.setattr(
        application.mission_executor,
        "_validate_site",
        lambda source, current: {"success": False, "issues": ["fixed failure"], "checks": []},
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
