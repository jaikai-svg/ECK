from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eck.app import build_application
from eck.brain.base import BrainResponse
from eck.domain.enums import MissionStatus, RuntimeSkillStatus
from eck.domain.models import (
    ActionProposal,
    MissionCompletionCreate,
    MissionCreate,
    MissionReviewDecision,
    MissionUpdate,
    RuntimeSkillManifest,
    SkillForgeRequest,
)


@pytest.mark.asyncio
async def test_mission_requires_creator_review_and_monthly_release(settings) -> None:
    application = build_application(settings)
    current = application.versions.status()
    application.store.update_runtime_version(
        major=current.major,
        minor=current.minor,
        patch=current.patch,
        verified_skill_count=current.verified_skill_count,
        next_minor_skill_count=current.next_minor_skill_count,
        pending_updates=1,
        reason="Tested update is waiting for a monthly release.",
    )
    mission = await application.missions.create(
        MissionCreate(
            title="每月能力考驗",
            objective="交付一個通過測試的隔離技能",
            completion_requirements="提供測試報告與可追溯成果",
            schedule="monthly",
            target_month="2026-08",
        )
    )
    submitted = await application.missions.submit_completion(
        mission.mission_id,
        MissionCompletionCreate(
            result_summary="技能已通過測試並產生報告",
            evidence=("workspace/report.json",),
        ),
    )
    assert submitted.status is MissionStatus.AWAITING_REVIEW

    approved = await application.missions.review(
        mission.mission_id,
        MissionReviewDecision(approved=True, feedback="人工二次驗證通過"),
    )

    assert approved.status is MissionStatus.APPROVED
    assert approved.approved_at is not None
    assert application.versions.status().major == current.major + 1


@pytest.mark.asyncio
async def test_foundation_skills_wait_safely_when_docker_is_off(settings) -> None:
    disabled = settings.model_copy(update={"skill_worker_enabled": False})
    application = build_application(disabled)
    skills = application.store.list_runtime_skills(limit=100)
    assert len(skills) == 6
    assert all(item.status is RuntimeSkillStatus.DRAFT for item in skills)

    result = await application.forge.validate_pending()

    assert len(result) == 6
    assert all(item["status"] == "draft" for item in result)


@pytest.mark.asyncio
async def test_worker_persists_execution_artifacts(settings, monkeypatch) -> None:
    application = build_application(settings)
    skill = next(
        item
        for item in application.store.list_runtime_skills(limit=100)
        if item.manifest.name == "image.process"
    )

    async def available() -> dict[str, object]:
        return {"available": True, "detail": "test"}

    async def image_available() -> bool:
        return True

    async def docker_run(**kwargs) -> dict[str, object]:
        output_dir = kwargs["output_dir"]
        (output_dir / "image.png").write_bytes(b"verified-image")
        return {"success": True, "result": {"artifact": "image.png"}}

    monkeypatch.setattr(application.worker, "health", available)
    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.worker, "_docker_run", docker_run)

    report = await application.worker.execute(skill, "create", {})

    artifact = settings.workspace_dir / report["artifacts"][0]
    assert artifact.read_bytes() == b"verified-image"
    assert report["artifact_dir"].startswith("runtime_artifacts/skill-run_")


def test_worker_image_entrypoint_has_safe_self_check() -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(project_root / "docker" / "skill-worker" / "runner.py")],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    report = json.loads(result.stdout.strip())
    assert result.returncode == 0
    assert report["success"] is True
    assert report["mode"] == "self-check"


@pytest.mark.asyncio
async def test_native_workspace_data_and_packaging_capabilities(application) -> None:
    workspace = application.registry.get("workspace.files")
    data = application.registry.get("data.analyze")
    package = application.registry.get("artifact.package")
    assert workspace is not None and data is not None and package is not None

    written = await workspace.execute(
        ActionProposal(
            capability="workspace.files",
            operation="write",
            payload={"path": "reports/value.txt", "content": "verified"},
        )
    )
    analyzed = await data.execute(
        ActionProposal(
            capability="data.analyze",
            operation="analyze",
            payload={"records": [{"value": 1}, {"value": 3}]},
        )
    )
    packaged = await package.execute(
        ActionProposal(
            capability="artifact.package",
            operation="package",
            payload={"name": "result", "files": ["reports/value.txt"]},
        )
    )

    assert written.success
    assert analyzed.output["numeric_columns"]["value"]["mean"] == 2
    assert packaged.success
    assert packaged.output["artifact"] == "artifacts/result.zip"


@pytest.mark.asyncio
async def test_mission_rejection_reopen_update_and_cancel(settings) -> None:
    application = build_application(settings)
    mission = await application.missions.create(
        MissionCreate(
            title="可修改課題",
            objective="建立並驗證一個計畫",
            completion_requirements="提交工具證據",
        )
    )
    updated = await application.missions.update(
        mission.mission_id,
        MissionUpdate(title="更新後課題"),
    )
    submitted = await application.missions.submit_completion(
        mission.mission_id,
        MissionCompletionCreate(result_summary="待驗收", evidence=("report.json",)),
    )
    rejected = await application.missions.review(
        mission.mission_id,
        MissionReviewDecision(approved=False, feedback="需要更多證據"),
    )
    reopened = await application.missions.reopen(mission.mission_id)
    cancelled = await application.missions.cancel(mission.mission_id)

    assert updated.title == "更新後課題"
    assert submitted.status is MissionStatus.AWAITING_REVIEW
    assert rejected.status is MissionStatus.REJECTED
    assert reopened.status is MissionStatus.ACTIVE
    assert cancelled.status is MissionStatus.CANCELLED


@pytest.mark.asyncio
async def test_generated_skill_is_scanned_tested_and_activated(application, monkeypatch) -> None:
    code = "def execute(operation, payload, context):\n    return {'success': True}\n"
    tests = "def test_execute():\n    assert True\n"

    async def chat(*args, **kwargs):
        return BrainResponse(
            content=json.dumps(
                {"code": code, "tests": tests, "improvements": ["isolated test"]}
            ),
            model="forge-test",
            raw={},
        )

    validations = 0

    async def validate(skill):
        nonlocal validations
        validations += 1
        return {"success": True, "tests": 1}

    monkeypatch.setattr(application.forge.brain, "chat", chat)
    monkeypatch.setattr(application.worker, "validate", validate)

    skill = await application.forge.forge(
        SkillForgeRequest(
            name="generated.audit",
            objective="建立可驗證的稽核技能",
            category="audit",
            operations=("execute",),
        )
    )
    status = await application.forge.status()

    assert skill.status is RuntimeSkillStatus.ACTIVE
    assert skill.manifest.generated
    generated_tests = Path(skill.source_dir, "test_skill.py").read_text(encoding="utf-8")
    assert generated_tests.startswith("from skill import execute")
    assert status["active"] >= 1
    assert status["canary_replays"] == 2
    assert validations == 2
    assert skill.test_report["canary"]["passed"] is True
    assert application.forge._next_version([]) == "0.1.0"
    assert application.forge._next_version(["0.1.0"]) == "0.1.1"
    with pytest.raises(ValueError, match="Blocked import"):
        application.forge._security_scan("import socket", ())
    with pytest.raises(ValueError, match="dynamic execution"):
        application.forge._security_scan("eval('1')", ())
    assert application.forge._ensure_test_import(
        "from skill import execute\n\ndef test_execute():\n    assert execute\n"
    ).count("from skill import execute") == 1


@pytest.mark.asyncio
async def test_failed_generated_skill_repairs_and_retests_without_kernel_restart(
    application, monkeypatch
) -> None:
    responses = iter(
        (
            {
                "code": "def execute(operation, payload, context):\n    return missing\n",
                "tests": "def test_execute():\n    assert False\n",
                "improvements": ["initial candidate"],
            },
            {
                "code": (
                    "def execute(operation, payload, context):\n"
                    "    return {'success': True}\n"
                ),
                "tests": "def test_execute():\n    assert True\n",
                "improvements": ["fixed failing return path"],
            },
        )
    )
    validations = iter((False, True, True))

    async def chat(*args, **kwargs):
        return BrainResponse(
            content=json.dumps(next(responses)),
            model="repair-test",
            raw={},
        )

    async def validate(skill):
        success = next(validations)
        return {"success": success, "tests": 1, "detail": "pass" if success else "fail"}

    monkeypatch.setattr(application.forge.brain, "chat", chat)
    monkeypatch.setattr(application.worker, "validate", validate)

    skill = await application.forge.forge(
        SkillForgeRequest(
            name="generated.repairable",
            objective="建立會先失敗再依錯誤報告自動修復的技能",
            category="development",
            operations=("execute",),
        )
    )
    versions = [
        item
        for item in application.store.list_runtime_skills(limit=100)
        if item.manifest.name == "generated.repairable"
    ]

    assert skill.status is RuntimeSkillStatus.ACTIVE
    assert skill.manifest.version == "0.1.1"
    assert {item.status for item in versions} == {
        RuntimeSkillStatus.ACTIVE,
        RuntimeSkillStatus.RETIRED,
    }


@pytest.mark.asyncio
async def test_missing_generated_test_import_is_repaired_without_model_call(
    application,
    monkeypatch,
) -> None:
    source_dir = application.settings.workspace_dir / "runtime_skills" / "missing-import" / "0.1.0"
    source_dir.mkdir(parents=True)
    manifest = RuntimeSkillManifest(
        name="generated.missing_import",
        version="0.1.0",
        description="Generated skill with a missing test import.",
        category="development",
        operations=("execute",),
        generated=True,
    )
    (source_dir / "skill.py").write_text(
        "def execute(operation, payload, context):\n    return {'success': True}\n",
        encoding="utf-8",
    )
    (source_dir / "test_skill.py").write_text(
        "def test_execute():\n    assert execute('execute', {}, {})['success']\n",
        encoding="utf-8",
    )
    failed = application.store.add_runtime_skill(
        manifest,
        source_dir=str(source_dir),
        source="eck-generated",
        status=RuntimeSkillStatus.FAILED,
    )

    async def chat(*args, **kwargs):
        del args, kwargs
        raise AssertionError("Deterministic import repair must run before the model.")

    async def validate(skill):
        tests = Path(skill.source_dir, "test_skill.py").read_text(encoding="utf-8")
        return {"success": tests.startswith("from skill import execute")}

    monkeypatch.setattr(application.forge.brain, "chat", chat)
    monkeypatch.setattr(application.worker, "validate", validate)

    repaired = await application.forge.repair_failed_skill(failed.runtime_skill_id)

    assert repaired.status is RuntimeSkillStatus.ACTIVE
    assert repaired.manifest.version == "0.1.1"


@pytest.mark.asyncio
async def test_worker_reports_disabled_missing_image_and_unsupported_operation(
    settings,
    monkeypatch,
) -> None:
    application = build_application(settings.model_copy(update={"skill_worker_enabled": False}))
    skill = application.store.list_runtime_skills(limit=1)[0]

    health = await application.worker.health()
    cached = await application.worker.health()
    unsupported = await application.worker.execute(skill, "unknown", {})

    assert not health["available"] and cached == health
    assert unsupported["success"] is False

    async def available():
        return {"available": True, "detail": "ready"}

    async def missing_image():
        return False

    monkeypatch.setattr(application.worker, "health", available)
    monkeypatch.setattr(application.worker, "image_available", missing_image)
    report = await application.worker.validate(skill)

    assert report["worker_unavailable"]


@pytest.mark.asyncio
async def test_worker_image_status_preserves_docker_diagnostics(
    application,
    monkeypatch,
) -> None:
    class Process:
        returncode = 1

        async def communicate(self):
            return b"", b"No such image: eck-skill-worker:0.1.0"

    async def create_process(*args, **kwargs):
        assert "inspect" in args
        assert kwargs
        return Process()

    monkeypatch.setattr("eck.runtime.worker.shutil.which", lambda _: "docker")
    monkeypatch.setattr("eck.runtime.worker.asyncio.create_subprocess_exec", create_process)

    status = await application.worker.image_status()

    assert status["available"] is False
    assert status["returncode"] == 1
    assert "No such image" in status["detail"]
