from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from eck.app import build_application
from eck.brain.base import BrainResponse
from eck.core.ids import new_id
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import (
    ActionProposal,
    CoreCandidateRequest,
    RuntimeSkillManifest,
    SkillForgeRequest,
)
from eck.services.core_evolution import CoreEvolutionLabService
from eck.services.identity import IdentityService


def test_soul_identity_is_persistent_hashed_and_revisioned(settings) -> None:
    identity = IdentityService(settings)
    first = identity.status()

    assert first["identity"] == "eck-test"
    assert first["integrity_valid"] is True
    assert first["revision"] == 1
    assert first["soul_id"].startswith("soul_")

    identity.soul_path.write_text(first["content"] + "\nOwner note.\n", encoding="utf-8")
    second = IdentityService(settings).status()

    assert second["soul_id"] == first["soul_id"]
    assert second["revision"] == 2
    assert second["previous_content_sha256"] == first["content_sha256"]
    assert second["integrity_valid"] is True


@pytest.mark.asyncio
async def test_repository_self_model_is_queryable_through_capability(application) -> None:
    model = application.self_model.refresh()
    capability = application.registry.get("core.self_inspect")

    assert model["initialized"] is True
    assert model["summary"]["python_modules"] > 0
    assert any(
        item["name"] == "services" for item in model["architecture"]["partitions"]
    )
    assert capability is not None

    result = await capability.execute(
        ActionProposal(
            capability="core.self_inspect",
            operation="query",
            payload={"query": "SkillForgeService"},
        )
    )

    assert result.success is True
    assert result.output["matches"]
    assert result.output["metrics"]["completed"] is True


def test_repository_self_model_refreshes_when_git_source_changes(
    application,
    monkeypatch,
) -> None:
    application.self_model.model_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-09T00:00:00+00:00",
                "git": {
                    "available": True,
                    "commit": "old",
                    "dirty": False,
                    "changed_paths": [],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        application.self_model,
        "_git_state",
        lambda: {
            "available": True,
            "commit": "new",
            "dirty": False,
            "changed_paths": [],
        },
    )
    refreshed = {
        "initialized": True,
        "git": {"commit": "new"},
        "summary": {},
    }
    monkeypatch.setattr(application.self_model, "refresh", lambda: refreshed)

    assert application.self_model.ensure() == refreshed


@pytest.mark.asyncio
async def test_skill_bridge_does_not_claim_growth_without_worker(application, monkeypatch) -> None:
    async def unavailable_worker() -> dict[str, object]:
        return {"available": False, "detail": "test worker unavailable"}

    monkeypatch.setattr(application.worker, "health", unavailable_worker)

    result = await application.skill_bridge.run_if_needed(force=True)
    status = await application.skill_bridge.status()

    assert result["status"] == "waiting_worker"
    assert status["conversion_verified"] is False
    assert status["active_generated_skills"] == 0
    assert application.store.list_events(limit=20)[-1].event_type == (
        "ResearchSkillBridgeEvaluated"
    )


@pytest.mark.asyncio
async def test_skill_bridge_requires_supplied_research_evidence(application, monkeypatch) -> None:
    research = [
        {
            "run_id": "research_1",
            "topic": "agent validation",
            "conclusion": "A deterministic parser can close the gap.",
            "confidence": 0.9,
            "claims": [],
            "sources": [{"canonical_url": "https://example.com/one"}],
        }
    ]

    async def chat(*args, **kwargs) -> BrainResponse:
        del args, kwargs
        return BrainResponse(
            content=(
                '{"decision":"forge","reason":"bounded gap","name":"research.parser",'
                '"objective":"Create a deterministic research result parser.",'
                '"category":"research","operations":["parse"],"permissions":[],'
                '"dependencies":[],"evidence_run_ids":["invented_run"]}'
            ),
            model="test-model",
            raw={},
        )

    monkeypatch.setattr(application.brain, "chat", chat)
    monkeypatch.setattr(application.self_model, "ensure", lambda: {"summary": {}})

    request, evidence, reason = await application.skill_bridge._propose(research)

    assert request is None
    assert evidence == []
    assert reason == "bounded gap"


@pytest.mark.asyncio
async def test_skill_bridge_records_insufficient_research(application, monkeypatch) -> None:
    async def available_worker() -> dict[str, object]:
        return {"available": True, "detail": "ready"}

    async def image_available() -> bool:
        return True

    monkeypatch.setattr(application.worker, "health", available_worker)
    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.skill_bridge, "_qualified_research_runs", lambda: [])

    result = await application.skill_bridge.run_if_needed(force=True)

    assert result["status"] == "insufficient_research_evidence"
    assert result["qualified_research_runs"] == 0


@pytest.mark.asyncio
async def test_skill_bridge_revalidates_recoverable_candidate(application, monkeypatch) -> None:
    async def available_worker() -> dict[str, object]:
        return {"available": True, "detail": "ready"}

    async def image_available() -> bool:
        return True

    manifest = RuntimeSkillManifest(
        name="research.recoverable",
        version="0.1.0",
        description="A recoverable generated research skill.",
        category="research",
        operations=("inspect",),
        generated=True,
    )
    skill = application.store.add_runtime_skill(
        manifest,
        source_dir=str(application.settings.workspace_dir / "recoverable"),
        source="eck-generated",
        status=RuntimeSkillStatus.DRAFT,
    )

    async def validate(runtime_skill_id: str) -> dict[str, object]:
        assert runtime_skill_id == skill.runtime_skill_id
        return {
            "runtime_skill_id": runtime_skill_id,
            "status": RuntimeSkillStatus.ACTIVE.value,
        }

    monkeypatch.setattr(application.worker, "health", available_worker)
    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.forge, "validate_skill", validate)

    result = await application.skill_bridge.run_if_needed(force=True)

    assert result["status"] == "revalidated_existing_candidate"
    assert result["runtime_skill"]["status"] == "active"


@pytest.mark.asyncio
async def test_skill_bridge_activates_evidence_bound_skill(application, monkeypatch) -> None:
    async def available_worker() -> dict[str, object]:
        return {"available": True, "detail": "ready"}

    async def image_available() -> bool:
        return True

    research = [{"run_id": f"research_{index}"} for index in range(12)]
    request = SkillForgeRequest(
        name="research.verified_converter",
        objective="Convert verified research into a deterministic local result.",
        category="research",
        operations=("convert",),
    )
    activated = application.store.add_runtime_skill(
        RuntimeSkillManifest(
            name=request.name,
            version="0.1.0",
            description=request.objective,
            category=request.category,
            operations=request.operations,
            generated=True,
        ),
        source_dir=str(application.settings.workspace_dir / "verified-converter"),
        source="eck-generated",
        status=RuntimeSkillStatus.ACTIVE,
    )

    async def propose(items):
        assert len(items) == 12
        return request, ["research_0"], "verified gap"

    async def forge(value: SkillForgeRequest):
        assert value == request
        return activated

    monkeypatch.setattr(application.worker, "health", available_worker)
    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.skill_bridge, "_qualified_research_runs", lambda: research)
    monkeypatch.setattr(application.skill_bridge, "_propose", propose)
    monkeypatch.setattr(application.forge, "forge", forge)

    result = await application.skill_bridge.run_if_needed(force=True)

    assert result["status"] == "skill_activated"
    assert result["runtime_skill"]["runtime_skill_id"] == activated.runtime_skill_id


@pytest.mark.asyncio
async def test_skill_bridge_repairs_failed_candidate_with_bounded_attempts(
    application,
    monkeypatch,
) -> None:
    async def available_worker() -> dict[str, object]:
        return {"available": True, "detail": "ready"}

    async def image_available() -> bool:
        return True

    failed = application.store.add_runtime_skill(
        RuntimeSkillManifest(
            name="research.failed_repair",
            version="0.1.0",
            description="A failed research-derived skill that needs one repair.",
            category="research",
            operations=("convert",),
            generated=True,
        ),
        source_dir=str(application.settings.workspace_dir / "failed-repair"),
        source="eck-generated",
        status=RuntimeSkillStatus.FAILED,
    )
    repaired = application.store.add_runtime_skill(
        failed.manifest.model_copy(update={"version": "0.1.1"}),
        source_dir=str(application.settings.workspace_dir / "repaired"),
        source="eck-generated",
        status=RuntimeSkillStatus.ACTIVE,
    )

    async def repair(runtime_skill_id: str):
        assert runtime_skill_id == failed.runtime_skill_id
        return repaired

    monkeypatch.setattr(application.worker, "health", available_worker)
    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.forge, "repair_failed_skill", repair)

    result = await application.skill_bridge.run_if_needed(force=True)

    assert result["status"] == "skill_repaired_and_activated"
    assert result["runtime_skill"]["runtime_skill_id"] == repaired.runtime_skill_id


def test_skill_bridge_does_not_count_byte_identical_repair_as_progress(
    application,
) -> None:
    records = []
    for version in ("0.1.0", "0.1.1"):
        source = application.settings.workspace_dir / "identical" / version
        source.mkdir(parents=True)
        (source / "skill.py").write_text(
            "def execute(*args):\n    return True\n",
            encoding="utf-8",
        )
        (source / "test_skill.py").write_text(
            "def test_skill():\n    assert True\n",
            encoding="utf-8",
        )
        records.append(
            application.store.add_runtime_skill(
                RuntimeSkillManifest(
                    name="research.identical_repair",
                    version=version,
                    description="Byte-identical generated repair candidate.",
                    category="research",
                    operations=("execute",),
                    generated=True,
                ),
                source_dir=str(source),
                source="eck-generated",
                status=RuntimeSkillStatus.FAILED,
            )
        )

    assert application.skill_bridge._repair_attempts(records[-1]) == 0


def test_core_candidate_changes_are_confined_to_isolated_targets(
    application,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    target = candidate / "src" / "eck" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    request = CoreCandidateRequest(
        objective="Improve the isolated example without changing the live core.",
        target_files=("src/eck/example.py",),
    )

    records = application.core_lab._apply_changes(
        request,
        candidate,
        request.target_files,
        [{"path": "src/eck/example.py", "content": "VALUE = 2\n"}],
    )

    assert records[0]["before_sha256"] != records[0]["after_sha256"]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert application.core_lab.status()["live_core_mutation"] is False

    with pytest.raises(ValueError, match="unapproved path"):
        application.core_lab._apply_changes(
            request,
            candidate,
            request.target_files,
            [{"path": "src/eck/other.py", "content": "VALUE = 3\n"}],
        )


def test_core_candidate_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="Unsafe candidate path"):
        CoreEvolutionLabService._validate_relative_path("../src/eck/app.py")


@pytest.mark.asyncio
async def test_core_candidate_draft_is_persisted_without_live_mutation(
    application,
    settings,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    source = project / "src" / "eck" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    (project / ".git").mkdir()
    lab = CoreEvolutionLabService(
        settings,
        application.events,
        application.brain,
        application.self_model,
        project_root=project,
    )

    def fake_git(root: Path, *arguments: str) -> str:
        del root
        if arguments[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if arguments[:2] == ("status", "--porcelain"):
            return ""
        if arguments[:2] == ("worktree", "add"):
            checkout = Path(arguments[3])
            target = checkout / "src" / "eck" / "example.py"
            target.parent.mkdir(parents=True)
            target.write_text("VALUE = 1\n", encoding="utf-8")
            return "prepared"
        if arguments[:2] == ("diff", "--binary"):
            return "diff --git a/src/eck/example.py b/src/eck/example.py\n"
        raise AssertionError(arguments)

    async def draft(*args, **kwargs):
        del args, kwargs
        return (
            [{"path": "src/eck/example.py", "content": "VALUE = 2\n"}],
            "minimal verified change",
            ["pytest"],
            "test-model",
        )

    async def validate(candidate_id: str):
        return lab.get_candidate(candidate_id)

    monkeypatch.setattr(lab, "_git", fake_git)
    monkeypatch.setattr(lab, "_draft_changes", draft)
    monkeypatch.setattr(lab, "validate_candidate", validate)
    monkeypatch.setattr(
        application.self_model,
        "refresh",
        lambda: {"source_tree_sha256": "b" * 64, "summary": {}},
    )

    result = await lab.create_candidate(
        CoreCandidateRequest(
            objective="Create a minimal isolated change and preserve the live source.",
            target_files=("src/eck/example.py",),
        )
    )

    assert result["status"] == "drafted"
    assert result["source_commit"] == "a" * 40
    assert result["patch_sha256"]
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert lab.list_candidates()[0]["candidate_id"] == result["candidate_id"]


@pytest.mark.asyncio
async def test_core_candidate_drafting_and_fixed_validation_gates(
    application,
    settings,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "candidate-project"
    target = project / "src" / "eck" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    lab = CoreEvolutionLabService(
        settings,
        application.events,
        application.brain,
        application.self_model,
        project_root=project,
    )
    response = BrainResponse(
        content=json.dumps(
            {
                "changes": [{"path": "src/eck/example.py", "content": "VALUE = 2\n"}],
                "rationale": "minimal",
                "tests": ["pytest"],
            }
        ),
        model="test-model",
        raw={},
    )

    async def chat(*args, **kwargs):
        del args, kwargs
        return response

    monkeypatch.setattr(application.brain, "chat", chat)
    monkeypatch.setattr(application.self_model, "ensure", lambda: {"summary": {}})
    changes, rationale, tests, model = await lab._draft_changes(
        CoreCandidateRequest(
            objective="Draft a minimal isolated candidate for fixed gate validation.",
            target_files=("src/eck/example.py",),
        ),
        project,
        ("src/eck/example.py",),
    )

    assert changes[0]["content"] == "VALUE = 2\n"
    assert (rationale, tests, model) == ("minimal", ["pytest"], "test-model")

    candidate_id = new_id("core-candidate")
    metadata = lab._candidate_metadata_dir(candidate_id)
    metadata.mkdir(parents=True)
    manifest = {
        "candidate_id": candidate_id,
        "project_path": str(project),
        "changed_files": [{"path": "src/eck/example.py"}],
        "status": "drafted",
        "created_at": "2026-08-09T00:00:00+00:00",
    }
    lab._write_manifest(metadata, manifest)

    async def passing_gate(name: str, command: list[str], cwd: Path):
        assert command
        assert cwd == project
        return {"name": name, "status": "passed", "returncode": 0}

    monkeypatch.setattr(lab, "_run_gate", passing_gate)
    validated = await lab.validate_candidate(candidate_id)

    assert validated["status"] == "validated_awaiting_human"
    assert validated["validation"]["passed"] is True
    assert validated["activated"] is False
    assert lab.get_candidate(candidate_id)["status"] == "validated_awaiting_human"


@pytest.mark.asyncio
async def test_core_gate_runner_reports_pass_and_json_parser_handles_fences(
    application,
    tmp_path: Path,
) -> None:
    gate = await application.core_lab._run_gate(
        "python",
        [sys.executable, "-c", "print('ok')"],
        tmp_path,
    )

    assert gate["status"] == "passed"
    assert "ok" in gate["output_tail"]
    assert CoreEvolutionLabService._json_object("```json\n{\"ok\": true}\n```") == {
        "ok": True
    }
    assert CoreEvolutionLabService._json_object("not json") == {}


@pytest.mark.asyncio
async def test_kernel_schedules_skill_bridge_once_without_spinning(settings, monkeypatch) -> None:
    configured = settings.model_copy(
        update={
            "research_skill_bridge_enabled": True,
            "research_skill_bridge_initial_delay_seconds": 0.01,
            "research_skill_bridge_interval_seconds": 30,
            "task_poll_seconds": 0.01,
        }
    )
    application = build_application(configured)
    calls = 0

    async def run_once(*, force: bool = False) -> dict[str, object]:
        nonlocal calls
        del force
        calls += 1
        return {"status": "test"}

    monkeypatch.setattr(application.skill_bridge, "run_if_needed", run_once)
    monkeypatch.setattr(
        application.resources,
        "background_allowed",
        lambda: (True, {"level": "normal", "background_allowed": True}),
    )

    await application.kernel.start()
    await asyncio.sleep(0.15)
    await application.kernel.stop()

    assert calls == 1
