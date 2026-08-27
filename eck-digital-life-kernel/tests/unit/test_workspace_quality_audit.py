from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eck.api.main import create_api
from eck.domain.enums import KernelPhase, RuntimeSkillStatus
from eck.domain.models import MissionCreate, MissionUpdate, RuntimeSkillManifest
from eck.modules.artifacts.deletion import ArtifactDeletionError
from eck.services.demos import DemoService
from eck.services.workspace import WorkspaceReadService


def _mission(title: str = "Quality audit project") -> MissionCreate:
    return MissionCreate(
        title=title,
        objective="Verify editable project state and rollback history.",
        completion_requirements="Preserve every edit and prove rollback.",
        target_month="2026-09",
        execution_kind="software_project",
    )


def _runtime_manifest(name: str) -> RuntimeSkillManifest:
    return RuntimeSkillManifest(
        name=name,
        version="1.0.0",
        description="A deterministic audit-only runtime skill.",
        category="audit",
        operations=("run",),
    )


def test_home_and_skill_page_use_the_same_available_skill_definition(
    application, tmp_path: Path
) -> None:
    application.store.upsert_skill_success(
        fingerprint="active-memory",
        name="Active memory skill",
        capability="audit.active",
        procedure={},
        verification_basis={},
        activation_threshold=1,
    )
    application.store.upsert_skill_success(
        fingerprint="inactive-memory",
        name="Inactive memory skill",
        capability="audit.inactive",
        procedure={},
        verification_basis={},
        activation_threshold=2,
    )
    runtime_dir = tmp_path / "runtime-active"
    runtime_dir.mkdir()
    application.store.add_runtime_skill(
        _runtime_manifest("audit.runtime.active"),
        source_dir=str(runtime_dir),
        source="human",
        status=RuntimeSkillStatus.ACTIVE,
    )

    workspace = WorkspaceReadService(application)
    home = workspace.home()
    skills = workspace.skills(limit=24, offset=0, phase=None)

    assert home["learning"]["available_skills"] == 2
    assert home["learning"]["memory_skills"] == 1
    assert home["learning"]["total_memory_skills"] == 2
    assert skills["counts"]["available"] == home["learning"]["available_skills"]


@pytest.mark.asyncio
async def test_library_deduplicates_claims_and_only_counts_real_open_questions(
    application,
) -> None:
    await DemoService(application).safe_code()
    first = application.library.page(limit=24, offset=0)
    stable_id = first["items"][0]["knowledge_id"]
    domain = application.library_authoring.create_domain(
        title="Duplicate stability",
        description="Keep one stable card per normalized claim.",
        knowledge_selector={"capability_prefixes": ["python.safe_expression"]},
    )
    await DemoService(application).safe_code()

    page = application.library.page(limit=24, offset=0)
    application.library_authoring.sync_domain(domain["domain_id"])
    bound_ids = application.store.list_domain_knowledge_ids(domain["domain_id"])

    assert page["page"]["total"] == 1
    assert page["items"][0]["knowledge_id"] == stable_id
    assert page["items"][0]["occurrence_count"] == 2
    assert page["items"][0]["unresolved_questions"] == []
    assert page["book"]["unresolved_question_count"] == 0
    assert page["cache"]["hit"] is False
    assert json.loads(application.library.catalog_path.read_text(encoding="utf-8"))[
        "projection_revision"
    ] == 2
    assert bound_ids == [stable_id]


@pytest.mark.asyncio
async def test_project_edits_are_versioned_and_can_clear_and_rollback_target_month(
    application,
) -> None:
    mission = application.store.create_mission(_mission())

    updated = await application.missions.update(
        mission.mission_id,
        MissionUpdate(
            title="Quality audit project revised",
            target_month=None,
            edit_reason="Clarify the audit scope and remove the deadline.",
        ),
    )
    revisions = application.store.list_mission_revisions(mission.mission_id)

    assert updated.title == "Quality audit project revised"
    assert updated.target_month is None
    assert revisions[0]["changed_fields"] == ["title", "target_month"]

    restored = await application.missions.rollback_revision(
        mission.mission_id,
        revisions[0]["revision_id"],
        reason="Restore the original accepted project definition.",
    )
    history = application.store.list_mission_revisions(mission.mission_id)

    assert restored.title == mission.title
    assert restored.target_month == "2026-09"
    assert history[0]["rollback_of_revision_id"] == revisions[0]["revision_id"]
    assert len(history) == 2


@pytest.mark.asyncio
async def test_sleep_cycle_persists_real_phases_results_and_changes(application) -> None:
    application.kernel.phase = KernelPhase.RUNNING

    queued = await application.kernel.request_sleep()
    completed = await application.kernel.run_sleep_cycle()

    assert completed is not None
    assert completed["run_id"] == queued["run_id"]
    assert completed["status"] == "completed"
    assert completed["phase"] == "completed"
    assert completed["result"]["event_chain_valid"] is True
    assert completed["result"]["consolidation_actions"] == []
    assert set(completed["changes"]) >= {
        "experiences",
        "knowledge_items",
        "active_memory_skills",
        "active_runtime_skills",
    }
    assert application.store.latest_sleep_run() == completed
    assert application.kernel.phase is KernelPhase.RUNNING


@pytest.mark.asyncio
async def test_queued_sleep_run_resumes_after_kernel_restart(application) -> None:
    queued = application.store.create_sleep_run(trigger_kind="manual")

    await application.kernel.start()
    for _ in range(40):
        latest = application.store.latest_sleep_run()
        if latest and latest["status"] == "completed":
            break
        await asyncio.sleep(0.05)
    await application.kernel.stop()

    latest = application.store.latest_sleep_run()
    assert latest is not None
    assert latest["run_id"] == queued["run_id"]
    assert latest["status"] == "completed"


def test_artifact_purge_covers_local_nas_cache_sidecars_and_derived_files(
    application, tmp_path: Path
) -> None:
    application.settings.archive_root = tmp_path / "nas"
    application.settings.archive_root.mkdir()
    parent = application.settings.export_dir / "quality-parent.txt"
    parent.write_text("parent", encoding="utf-8")
    parent.with_suffix(".json").write_text(
        json.dumps({"title": "Quality parent"}), encoding="utf-8"
    )
    application.artifacts.refresh_if_due(force=True)
    parent_artifact = next(
        item
        for item in application.store.list_all_artifacts()
        if item["local_path"] == str(parent.resolve())
    )
    derived = application.settings.export_dir / "quality-derived.txt"
    derived.write_text("derived", encoding="utf-8")
    derived.with_suffix(".json").write_text(
        json.dumps(
            {
                "title": "Quality derived",
                "parent_artifact_id": parent_artifact["artifact_id"],
            }
        ),
        encoding="utf-8",
    )
    application.artifacts.refresh_if_due(force=True)
    derived_artifact = next(
        item
        for item in application.store.list_all_artifacts()
        if item["local_path"] == str(derived.resolve())
    )
    archive = application.archive.archive(
        parent_artifact["artifact_id"], remove_local=True
    )
    archived_page = application.artifacts.page(
        limit=10,
        offset=0,
        storage_state="nas",
    )
    assert parent_artifact["artifact_id"] in {
        item["artifact_id"] for item in archived_page["items"]
    }
    cache_path = application.archive.acquire(parent_artifact["artifact_id"])
    application.archive.release(parent_artifact["artifact_id"])

    plan = application.artifact_deletion.plan(parent_artifact["artifact_id"])
    assert plan["deletable"] is True
    assert set(plan["artifact_ids"]) == {
        parent_artifact["artifact_id"],
        derived_artifact["artifact_id"],
    }
    assert {item["role"] for item in plan["targets"]} >= {
        "archive",
        "cache",
        "sidecar",
        "local",
    }

    result = application.artifact_deletion.purge(
        parent_artifact["artifact_id"],
        plan_sha256=plan["plan_sha256"],
        confirm_title="Quality parent",
    )

    assert result["status"] == "completed"
    assert not Path(archive["archive_path"]).exists()
    assert not cache_path.exists()
    assert not parent.with_suffix(".json").exists()
    assert not derived.exists()
    assert not derived.with_suffix(".json").exists()
    for artifact_id in plan["artifact_ids"]:
        with pytest.raises(KeyError):
            application.store.get_artifact(artifact_id)


def test_artifact_purge_restores_quarantine_when_database_commit_fails(
    application, monkeypatch
) -> None:
    source = application.settings.export_dir / "rollback-result.txt"
    source.write_text("must survive", encoding="utf-8")
    application.artifacts.refresh_if_due(force=True)
    artifact = next(
        item
        for item in application.store.list_all_artifacts()
        if item["local_path"] == str(source.resolve())
    )
    plan = application.artifact_deletion.plan(artifact["artifact_id"])
    original_finish = application.store.finish_artifact_deletion
    calls = 0

    def fail_first_commit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated database commit failure")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(application.store, "finish_artifact_deletion", fail_first_commit)

    with pytest.raises(ArtifactDeletionError):
        application.artifact_deletion.purge(
            artifact["artifact_id"],
            plan_sha256=plan["plan_sha256"],
            confirm_title=artifact["title"],
        )

    assert source.read_text(encoding="utf-8") == "must survive"
    assert application.store.get_artifact(artifact["artifact_id"])


def test_artifact_dates_come_from_files_and_date_filters_include_the_whole_day(
    application,
) -> None:
    source = application.settings.export_dir / "dated-result.txt"
    source.write_text("dated", encoding="utf-8")
    modified = datetime(2026, 8, 10, 15, 30, tzinfo=UTC)
    os.utime(source, (modified.timestamp(), modified.timestamp()))

    application.artifacts.refresh_if_due(force=True)
    page = application.artifacts.page(
        limit=10,
        offset=0,
        created_from="2026-08-10",
        created_to="2026-08-10",
    )

    artifact = next(item for item in page["items"] if item["title"] == "dated result")
    assert artifact["created_at"].startswith("2026-08-10T15:30")


def test_quality_audit_rest_endpoints_are_present_and_operational(application) -> None:
    mission = application.store.create_mission(_mission("API quality audit"))
    result_file = application.settings.export_dir / "api-delete.txt"
    result_file.write_text("delete through API", encoding="utf-8")
    application.artifacts.refresh_if_due(force=True)
    artifact = next(
        item
        for item in application.store.list_all_artifacts()
        if item["local_path"] == str(result_file.resolve())
    )

    with TestClient(create_api(application=application)) as client:
        patched = client.patch(
            f"/v1/missions/{mission.mission_id}",
            json={
                "objective": "Verify API-backed project editing and rollback.",
                "edit_reason": "Exercise the public edit contract.",
            },
        )
        assert patched.status_code == 200
        revisions = client.get(f"/v1/missions/{mission.mission_id}/revisions")
        assert revisions.status_code == 200
        revision_id = revisions.json()["items"][0]["revision_id"]
        rollback = client.post(
            f"/v1/missions/{mission.mission_id}/revisions/{revision_id}/rollback",
            json={"reason": "Verify the public rollback contract."},
        )
        assert rollback.status_code == 200

        sleep = client.post("/v1/kernel/sleep")
        assert sleep.status_code == 200
        assert sleep.json()["run"]["status"] == "queued"
        assert client.get("/v1/kernel/sleep/status").json()["run"]["run_id"]

        plan = client.get(
            f"/v1/workspace/results/{artifact['artifact_id']}/deletion-plan"
        )
        assert plan.status_code == 200
        deleted = client.request(
            "DELETE",
            f"/v1/workspace/results/{artifact['artifact_id']}",
            json={
                "plan_sha256": plan.json()["plan_sha256"],
                "confirm_title": artifact["title"],
                "include_derived": True,
            },
        )
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "completed"


def test_workspace_quality_buttons_have_stable_api_contracts(application) -> None:
    paths = create_api(application=application).openapi()["paths"]
    required = {
        "/v1/kernel/resume",
        "/v1/kernel/pause",
        "/v1/kernel/sleep",
        "/v1/kernel/sleep/status",
        "/v1/missions/{mission_id}",
        "/v1/missions/{mission_id}/revisions",
        "/v1/missions/{mission_id}/revisions/{revision_id}/rollback",
        "/v1/workspace/results/{artifact_id}/deletion-plan",
        "/v1/workspace/results/{artifact_id}",
        "/v1/workspace/results/{artifact_id}/archive",
        "/v1/workspace/results/{artifact_id}/restore",
    }

    assert required <= set(paths)
