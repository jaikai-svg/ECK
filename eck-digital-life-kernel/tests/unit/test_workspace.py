from __future__ import annotations

from eck.domain.enums import MissionCycleStatus, MissionStatus
from eck.domain.models import MissionCreate, MissionStepDefinition
from eck.services.demos import DemoService
from eck.services.workspace import WorkspaceReadService


def _mission(title: str) -> MissionCreate:
    return MissionCreate(
        title=title,
        objective=f"Build and verify {title}",
        completion_requirements="Provide a reproducible artifact and test evidence.",
        execution_kind="software_project",
    )


def test_workspace_home_reuses_existing_authoritative_records(application) -> None:
    first = application.store.create_mission(_mission("Workspace project one"))
    second = application.store.create_mission(_mission("Workspace project two"))

    result = WorkspaceReadService(application).home()

    assert result["schema_version"] == "eck-workspace-home.v1"
    assert {item["project_id"] for item in result["running_projects"]} == {
        first.mission_id,
        second.mission_id,
    }
    assert result["activity"]["kind"] == "idle"
    assert result["refresh"] == {
        "busy": False,
        "poll_after_seconds": 30,
        "pause_when_hidden": True,
    }
    assert application.store.count_missions() == 2


def test_workspace_system_uses_cached_project_measurement(application, monkeypatch) -> None:
    monkeypatch.setattr(
        application.resources,
        "project_snapshot",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    result = WorkspaceReadService(application).system()

    assert result["schema_version"] == "eck-workspace-system.v1"
    assert result["resources"]["project"]["cached"] is True
    assert "forge" in result["services"]


def test_workspace_project_pagination_and_status_filter(application) -> None:
    missions = [
        application.store.create_mission(_mission(f"Project {index}"))
        for index in range(4)
    ]
    application.store.set_mission_status(missions[0].mission_id, MissionStatus.APPROVED)
    service = WorkspaceReadService(application)

    first_page = service.projects(limit=2, offset=0, status=None)
    second_page = service.projects(limit=2, offset=2, status=None)
    approved = service.projects(
        limit=12,
        offset=0,
        status=MissionStatus.APPROVED,
    )

    assert first_page["page"] == {
        "limit": 2,
        "offset": 0,
        "total": 4,
        "next_offset": 2,
    }
    assert second_page["page"]["next_offset"] is None
    assert approved["page"]["total"] == 1
    assert approved["items"][0]["status"] == "approved"


def test_workspace_project_exposes_structured_react_without_private_cot(application) -> None:
    mission = application.store.create_mission(_mission("Structured project"))
    application.store.create_mission_steps(
        mission.mission_id,
        (
            MissionStepDefinition(
                step_key="workspace.prepare",
                sequence=1,
                action_kind="workspace.prepare",
                objective="Prepare a bounded project workspace.",
            ),
        ),
    )
    claimed = application.store.claim_next_mission_step()
    assert claimed is not None
    cycle = application.store.create_mission_react_cycle(
        claimed,
        reason_summary="Prepare before implementation.",
        action={"kind": "workspace.prepare"},
    )
    application.store.finish_mission_react_cycle(
        cycle.cycle_id,
        status=MissionCycleStatus.SUCCEEDED,
        observation={"prepared": True},
        correction="No correction required.",
    )

    result = WorkspaceReadService(application).project(mission.mission_id)

    assert result["schema_version"] == "eck-workspace-project.v1"
    assert result["steps"][0]["step_key"] == "workspace.prepare"
    summary = result["react_summaries"][0]
    assert set(summary) >= {
        "goal",
        "plan",
        "action",
        "observation",
        "correction",
        "verification",
        "conclusion",
    }
    assert result["skill_usages"] == []
    assert "chain-of-thought" in result["thinking_policy"]
    assert "private" not in str(summary).lower()


async def test_library_projection_is_incremental_and_rebuildable(application) -> None:
    await DemoService(application).safe_code()

    first = application.library.page(limit=24, offset=0)
    second = application.library.page(limit=24, offset=0)

    assert first["schema_version"] == "eck-library.v1"
    assert first["source_authority"] == "knowledge_items + tasks + reflections"
    assert first["page"]["total"] == 1
    assert first["items"][0]["content_sha256"]
    assert first["items"][0]["revision_history"]
    assert first["cache"]["rebuildable"] is True
    assert second["cache"]["hit"] is True
    assert application.library.catalog_path.is_file()
    assert application.library.book_path.is_file()


async def test_workspace_skills_uses_lifecycle_and_verified_task_usage(application) -> None:
    await DemoService(application).safe_code()
    await DemoService(application).safe_code()

    result = WorkspaceReadService(application).skills(
        limit=24,
        offset=0,
        phase=None,
    )

    assert result["schema_version"] == "eck-workspace-skills.v1"
    assert result["source_authority"] == "skill-lifecycle.v1"
    assert result["counts"]["total"] >= 1
    skill = next(item for item in result["items"] if item["source_kind"] == "experience")
    assert skill["test_result"]
    assert skill["completed_task_count"] == 2
    assert len(skill["completed_tasks"]) == 2
