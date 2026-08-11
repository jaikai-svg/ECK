from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eck.app import build_application
from eck.config import Settings
from eck.domain.enums import (
    BenchmarkSuite,
    MissionCycleStatus,
    MissionStatus,
    RuntimeSkillStatus,
)
from eck.domain.models import (
    BenchmarkRunCreate,
    MissionCreate,
    MissionStepDefinition,
    RuntimeSkillManifest,
)


def _active_skill(application):
    source = application.settings.workspace_dir / "runtime_skills" / "travel.compose" / "1.0.0"
    source.mkdir(parents=True)
    manifest = RuntimeSkillManifest(
        name="travel.compose",
        version="1.0.0",
        description="Compose a verified travel plan from explicit constraints.",
        category="travel",
        operations=("compose",),
        generated=True,
    )
    source.joinpath("manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    source.joinpath("skill.py").write_text(
        "def execute(operation, payload, context):\n"
        "    return {'success': operation == 'compose'}\n",
        encoding="utf-8",
    )
    source.joinpath("test_skill.py").write_text(
        "from skill import execute\n\n"
        "def test_compose():\n"
        "    assert execute('compose', {}, {})['success']\n",
        encoding="utf-8",
    )
    return application.store.add_runtime_skill(
        manifest,
        source_dir=str(source),
        source="eck-generated",
        status=RuntimeSkillStatus.ACTIVE,
        test_report={"success": True, "canary": {"passed": True}},
    )


def _receiver_application(settings: Settings, tmp_path: Path):
    root = tmp_path / "receiver"
    values = settings.model_dump()
    values.update(
        identity="eck-receiver",
        data_dir=root / "data",
        workspace_dir=root / "workspace",
        export_dir=root / "workspace" / "exports",
        mission_workspace_dir=root / "workspace" / "missions",
        database_path=root / "data" / "eck.db",
        image_output_dir=root / "workspace" / "generated_images",
        video_output_dir=root / "workspace" / "generated_videos",
        forge_root=root / "workspace" / "forge",
        rembg_model_dir=root / "workspace" / "rembg" / "models",
        framepack_source_dir=root / "workspace" / "framepack" / "source",
        cogvideo_model_dir=root / "workspace" / "cogvideo" / "model",
        cogvideo_smoke_report=root / "workspace" / "cogvideo" / "verified-runtime.json",
    )
    return build_application(Settings(**values))


def _verified_learning_records(application) -> dict[str, str]:
    now = datetime.now(UTC)
    source = application.store.add_research_snapshot(
        canonical_url="https://example.com/verified-agent-study",
        url_sha256="1" * 64,
        raw_sha256="2" * 64,
        content_sha256="3" * 64,
        simhash="0" * 16,
        text="Verified agent workflows improve only when fixed tests are reproduced.",
        extraction_method="test",
        retain_until=now + timedelta(days=30),
        source_domain="example.com",
        title="Verified Agent Study",
        author="Researcher",
        provider="test",
        published_at=now.isoformat(),
        fetched_at=now,
        content_type="text/html",
        metadata={},
        near_duplicate_distance=3,
    )
    research_run_id = application.store.begin_research_run(
        action_id="federation-test",
        topic="verified agent engineering",
        seed_url=source["canonical_url"],
    )
    application.store.complete_research_run(
        research_run_id,
        status="completed",
        conclusion_status="supported",
        conclusion="Independent fixed tests are required before reuse.",
        confidence=0.9,
        queries=["verified agent engineering"],
        source_snapshot_ids=[source["snapshot_id"]],
        metrics={"source_count": 1},
        report={},
        claims=[
            {
                "claim": "Fixed tests are required before capability reuse.",
                "kind": "factual",
                "status": "supported",
                "confidence": 0.9,
                "rationale": "The source and reproduction agree.",
            }
        ],
        evidence_links=[
            {
                "claim_index": 0,
                "snapshot_id": source["snapshot_id"],
                "stance": "supports",
                "excerpt": "Fixed tests are reproduced.",
                "note": "Direct supporting evidence.",
                "independence_key": source["independence_key"],
            }
        ],
    )
    benchmark = application.store.add_benchmark_run(
        BenchmarkRunCreate(
            suite=BenchmarkSuite.REAL_TASKS,
            benchmark_version="federation-v1",
            model="local-test-model",
            model_artifact_hash="4" * 64,
            evaluator="fixed-test-runner",
            score=0.88,
            sample_count=25,
            protocol={"scope": "public-fixed-tests", "repetitions": 2},
            notes="No hidden answers are included.",
        )
    )
    mission = application.store.create_mission(
        MissionCreate(
            title="Build a verified agent workflow",
            objective="Build and test a reusable agent workflow.",
            completion_requirements="All fixed tests pass and a human approves the result.",
            execution_kind="software_project",
        )
    )
    application.store.create_mission_steps(
        mission.mission_id,
        (
            MissionStepDefinition(
                step_key="workflow.verify",
                sequence=1,
                action_kind="software.validate",
                objective="Run deterministic fixed tests for the agent workflow.",
            ),
        ),
    )
    step = application.store.claim_next_mission_step()
    assert step is not None
    cycle = application.store.create_mission_react_cycle(
        step,
        reason_summary="The workflow must be tested before it can be reused.",
        action={"kind": "software.validate", "scope": "fixed-tests"},
    )
    application.store.finish_mission_react_cycle(
        cycle.cycle_id,
        status=MissionCycleStatus.SUCCEEDED,
        observation={"success": True, "tests": 25},
        correction="Keep the deterministic validation gate.",
    )
    application.store.finish_mission_step(
        step.step_id,
        success=True,
        output={"success": True, "quality_score": 88},
    )
    application.store.set_mission_status(
        mission.mission_id,
        MissionStatus.APPROVED,
        progress={
            "project_type": "python_project",
            "learning_pattern": {
                "schema_version": "eck-mission-pattern.v1",
                "project_type": "python_project",
                "title": mission.title,
                "tags": ["agent", "workflow", "verification"],
                "review_lessons": ["Run fixed tests before reuse."],
                "quality_score": 88,
            },
        },
        approved_at=now,
    )
    return {
        "research_run_id": research_run_id,
        "benchmark_run_id": benchmark.run_id,
        "mission_id": mission.mission_id,
    }


@pytest.mark.asyncio
async def test_evolution_pack_exports_only_public_capability_and_stages_by_plan(
    application,
) -> None:
    skill = _active_skill(application)

    exported = await application.federation.export_skill(
        skill.runtime_skill_id,
        license_spdx="Apache-2.0",
        source_url="https://example.com/travel-compose",
    )
    archive = application.federation.outbox / exported["archive"]
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert "payload/skill.py" in names
        assert "payload/test_skill.py" in names
        assert not any("SOUL" in name or name.endswith("eck.db") for name in names)
    inbox = application.federation.inbox / archive.name
    shutil.copy2(archive, inbox)

    preview = application.federation.preview(inbox.name)
    staged = await application.federation.stage(
        inbox.name,
        plan_sha256=preview["plan_sha256"],
    )

    assert preview["verification"]["valid"] is True
    assert preview["plan"]["private_layers_touched"] == []
    assert preview["activation_allowed"] is False
    assert staged["status"] == "quarantined"
    assert application.federation.quarantine.joinpath(
        exported["pack_id"], "payload", "skill.py"
    ).is_file()


@pytest.mark.asyncio
async def test_evolution_pack_rejects_changed_plan_and_private_source(application) -> None:
    skill = _active_skill(application)
    source = application.settings.workspace_dir / "runtime_skills" / "travel.compose" / "1.0.0"
    source.joinpath("SOUL.md").write_text("private identity", encoding="utf-8")

    with pytest.raises(ValueError, match="Private file"):
        await application.federation.export_skill(
            skill.runtime_skill_id,
            license_spdx="Apache-2.0",
        )

    source.joinpath("SOUL.md").unlink()
    exported = await application.federation.export_skill(
        skill.runtime_skill_id,
        license_spdx="Apache-2.0",
    )
    archive = application.federation.outbox / exported["archive"]
    shutil.copy2(archive, application.federation.inbox / archive.name)
    with pytest.raises(ValueError, match="changed after preview"):
        await application.federation.stage(archive.name, plan_sha256="0" * 64)


@pytest.mark.asyncio
async def test_evolution_pack_reproduces_then_installs_through_local_canary(
    application,
    settings: Settings,
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = _active_skill(application)
    exported = await application.federation.export_skill(
        skill.runtime_skill_id,
        license_spdx="Apache-2.0",
    )
    archive = application.federation.outbox / exported["archive"]
    receiver = _receiver_application(settings, tmp_path)
    inbox = receiver.federation.inbox / archive.name
    shutil.copy2(archive, inbox)
    preview = receiver.federation.preview(inbox.name)
    staged = await receiver.federation.stage(
        inbox.name,
        plan_sha256=preview["plan_sha256"],
    )

    validation_calls = []

    async def validated(skill_record):
        validation_calls.append(skill_record.runtime_skill_id)
        return {
            "success": True,
            "worker_unavailable": False,
            "detail": "isolated test passed",
            "test_output": "1 passed",
        }

    monkeypatch.setattr(receiver.federation.forge.worker, "validate", validated)
    reproduced = await receiver.federation.reproduce(staged["pack_id"])
    installed = await receiver.federation.install(staged["pack_id"])
    adopted = receiver.store.get_runtime_skill(installed["runtime_skill_id"])

    assert reproduced["success"] is True
    assert installed["reproductions"] == 2
    assert installed["validation"]["status"] == RuntimeSkillStatus.ACTIVE.value
    assert adopted.source == "federation"
    assert adopted.status is RuntimeSkillStatus.ACTIVE
    assert len(validation_calls) == 3
    assert receiver.federation.status()["quarantined"] == 1


@pytest.mark.asyncio
async def test_evolution_pack_rejects_tampering_unsafe_archives_and_bad_exports(
    application,
) -> None:
    skill = _active_skill(application)
    with pytest.raises(ValueError, match="SPDX"):
        await application.federation.export_skill(skill.runtime_skill_id, license_spdx="?")
    with pytest.raises(ValueError, match="unsafe"):
        await application.federation.export_skill(
            skill.runtime_skill_id,
            license_spdx="Apache-2.0",
            source_url="https://user:secret@example.com/source",
        )

    exported = await application.federation.export_skill(
        skill.runtime_skill_id,
        license_spdx="Apache-2.0",
    )
    archive = application.federation.outbox / exported["archive"]
    tampered = application.federation.inbox / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "payload/skill.py":
                content += b"\n# untracked modification\n"
            target.writestr(info, content)

    verification = application.federation.verify(tampered.name)
    assert verification["valid"] is False
    assert "sha256:payload/skill.py" in verification["failures"]
    with pytest.raises(ValueError, match="verification failed"):
        application.federation.preview(tampered.name)

    unsafe = application.federation.inbox / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps({"format": "invalid"}))
        bundle.writestr("../escape.py", "pass")
    with pytest.raises(ValueError, match="unsafe path"):
        application.federation.verify(unsafe.name)
    with pytest.raises(ValueError, match="Invalid Evolution Pack name"):
        application.federation.pack_path("../unsafe.zip")
    with pytest.raises(ValueError, match="location"):
        application.federation.pack_path(archive.name, location="private")
    with pytest.raises(FileNotFoundError):
        application.federation.pack_path("missing.zip")


@pytest.mark.asyncio
async def test_evolution_pack_requires_successful_reproduction_before_install(
    application,
    monkeypatch,
) -> None:
    skill = _active_skill(application)
    exported = await application.federation.export_skill(
        skill.runtime_skill_id,
        license_spdx="Apache-2.0",
    )
    archive = application.federation.outbox / exported["archive"]
    shutil.copy2(archive, application.federation.inbox / archive.name)
    preview = application.federation.preview(archive.name)
    staged = await application.federation.stage(
        archive.name,
        plan_sha256=preview["plan_sha256"],
    )
    with pytest.raises(ValueError, match="has not run"):
        await application.federation.install(staged["pack_id"])

    async def rejected(_skill_record):
        return {
            "success": False,
            "worker_unavailable": False,
            "detail": "test failed",
        }

    monkeypatch.setattr(application.federation.forge.worker, "validate", rejected)
    reproduced = await application.federation.reproduce(staged["pack_id"])
    assert reproduced["success"] is False
    with pytest.raises(ValueError, match="did not pass"):
        await application.federation.install(staged["pack_id"])
    with pytest.raises(ValueError, match="Invalid Evolution Pack ID"):
        await application.federation.reproduce("not-a-pack")


@pytest.mark.asyncio
async def test_four_data_pack_types_export_verified_public_payloads(application) -> None:
    records = _verified_learning_records(application)
    exports = [
        await application.federation.export_knowledge(
            (records["research_run_id"],),
            license_spdx="Apache-2.0",
        ),
        await application.federation.export_strategy(
            records["mission_id"],
            license_spdx="Apache-2.0",
        ),
        await application.federation.export_evaluation(
            (records["benchmark_run_id"],),
            license_spdx="Apache-2.0",
        ),
        await application.federation.export_distillation(
            (records["mission_id"],),
            license_spdx="Apache-2.0",
        ),
    ]

    assert {item["pack_type"] for item in exports} == {
        "knowledge_pack",
        "strategy_pack",
        "evaluation_pack",
        "distillation_pack",
    }
    for exported in exports:
        assert exported["verification"]["valid"] is True
        archive = application.federation.outbox / exported["archive"]
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["privacy"] == {
                "soul": False,
                "private_memory": False,
                "owner_settings": False,
                "credentials": False,
                "machine_paths": False,
            }
            assert all(name.startswith("payload/") for name in manifest["files"])


@pytest.mark.asyncio
async def test_data_pack_reproduction_installs_reusable_learning_context(
    application,
    settings: Settings,
    tmp_path: Path,
) -> None:
    records = _verified_learning_records(application)
    exported = await application.federation.export_knowledge(
        (records["research_run_id"],),
        license_spdx="Apache-2.0",
    )
    receiver = _receiver_application(settings, tmp_path)
    archive = application.federation.outbox / exported["archive"]
    shutil.copy2(archive, receiver.federation.inbox / archive.name)
    preview = receiver.federation.preview(archive.name)
    staged = await receiver.federation.stage(
        archive.name,
        plan_sha256=preview["plan_sha256"],
    )

    reproduction = await receiver.federation.reproduce(staged["pack_id"])
    installed = await receiver.federation.install(staged["pack_id"])
    context = receiver.federation.learning_context("verified agent engineering")

    assert reproduction["success"] is True
    assert reproduction["metrics"] == {"runs": 1, "claims": 1, "sources": 1}
    assert installed["pack_type"] == "knowledge_pack"
    assert context["knowledge"][0]["conclusion_status"] == "supported"
    assert context["provenance"][0]["pack_id"] == staged["pack_id"]
    assert receiver.federation.synthesis_status()["status"] == (
        "collecting-complementary-evidence"
    )


@pytest.mark.asyncio
async def test_registry_requires_cosign_and_two_independent_reviews(
    application,
    monkeypatch,
) -> None:
    records = _verified_learning_records(application)
    exported = await application.federation.export_evaluation(
        (records["benchmark_run_id"],),
        license_spdx="Apache-2.0",
    )
    archive = application.federation.outbox / exported["archive"]
    application.federation.cosign.bundle_path(archive).write_text("{}", encoding="utf-8")

    def verified(_archive):
        return {
            "verified": True,
            "scheme": "sigstore-cosign",
            "bundle": f"{archive.name}.sigstore.json",
            "detail": "Verified OK",
        }

    monkeypatch.setattr(application.federation.cosign, "verify", verified)
    candidate = application.federation.submit_registry_candidate(archive.name)
    assert candidate["trust"]["admission_allowed"] is False

    for reviewer in ("a" * 64, "b" * 64):
        candidate = application.federation.review_registry_candidate(
            exported["pack_id"],
            reviewer_node_sha256=reviewer,
            verdict="approve",
            reproduction_success=True,
            fixed_test_delta=0.0,
            hidden_test_regression=False,
            permission_reviewed=True,
            dependency_reviewed=True,
            evidence_sha256="c" * 64,
            notes="Independent fixed and hidden tests passed.",
        )

    admitted = application.federation.admit_registry_candidate(exported["pack_id"])
    status = application.federation.capability_registry.status()

    assert candidate["trust"]["admission_allowed"] is True
    assert admitted["status"] == "admitted"
    assert admitted["trust"]["score"] == 100
    assert status["admitted"] == 1

    revoked = application.federation.revoke_registry_pack(
        exported["pack_id"],
        reason="A later independent review found a regression.",
    )
    assert revoked["status"] == "revoked"
    assert application.federation.capability_registry.status()["revoked"] == 1


def test_cosign_verification_requires_explicit_trust_policy(
    application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "pack.zip"
    archive.write_bytes(b"pack")
    application.federation.cosign.bundle_path(archive).write_text("{}", encoding="utf-8")
    public_key = tmp_path / "cosign.pub"
    public_key.write_text("public-key", encoding="utf-8")
    application.settings.federation_cosign_public_key_path = public_key
    monkeypatch.setattr(application.federation.cosign, "_executable", lambda: "cosign")
    monkeypatch.setattr(
        application.federation.cosign,
        "_run",
        lambda command: {"returncode": 0, "detail": "Verified OK"},
    )

    verified = application.federation.cosign.verify(archive)

    assert verified["verified"] is True
    assert verified["scheme"] == "sigstore-cosign"
