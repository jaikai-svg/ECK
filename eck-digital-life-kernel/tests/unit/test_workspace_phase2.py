from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eck.api.main import create_api
from eck.domain.enums import (
    ComparisonOperator,
    EvidenceSource,
    RiskLevel,
    RuntimeSkillStatus,
)
from eck.domain.models import (
    ActionProposal,
    RuntimeSkillManifest,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
)
from eck.modules.archive.service import ArchiveIntegrityError, ArchiveOfflineError
from eck.modules.library.authoring import LibraryReadinessError


def _safe_task(index: int, labels: tuple[str, ...]) -> TaskCreate:
    goal = f"Verify deterministic domain example {index}."
    return TaskCreate(
        goal=goal,
        success_contract=SuccessContract(
            goal=goal,
            checks=(
                VerificationCheck(
                    name="all tests pass",
                    path="metrics.all_passed",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
            ),
            required_evidence=(EvidenceSource.UNIT_TEST, EvidenceSource.FORMAL_CHECK),
            require_reproducible=True,
        ),
        action=ActionProposal(
            capability="python.safe_expression",
            operation="evaluate",
            payload={
                "expression": f"x + {index}",
                "cases": [{"input": index, "expected": index * 2}],
            },
            declared_risk=RiskLevel.LOW,
        ),
        labels=labels,
    )


@pytest.mark.asyncio
async def test_task_skill_usage_is_created_only_for_actual_worker_execution(
    application, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "runtime-skill"
    source.mkdir()
    manifest = RuntimeSkillManifest(
        name="test.actual.worker",
        version="1.0.0",
        description="Return a verified structured worker result.",
        category="test",
        operations=("run",),
    )
    skill = application.store.add_runtime_skill(
        manifest,
        source_dir=str(source),
        source="human",
        status=RuntimeSkillStatus.ACTIVE,
    )

    async def execute_worker(skill_record, operation, payload):
        assert skill_record.runtime_skill_id == skill.runtime_skill_id
        assert operation == "run"
        artifact = application.settings.export_dir / "worker-result.txt"
        artifact.write_text(str(payload["value"]), encoding="utf-8")
        return {
            "success": True,
            "value": payload["value"],
            "artifact_path": str(artifact.resolve()),
        }

    monkeypatch.setattr(application.worker, "execute", execute_worker)
    create = TaskCreate(
        goal="Execute the active isolated runtime skill.",
        success_contract=SuccessContract(
            goal="Execute the active isolated runtime skill.",
            checks=(
                VerificationCheck(
                    name="worker success",
                    path="success",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
            ),
            required_evidence=(EvidenceSource.TOOL,),
            require_reproducible=False,
        ),
        action=ActionProposal(
            capability="runtime.skill",
            operation="execute",
            payload={
                "skill_name": manifest.name,
                "operation": "run",
                "input": {"value": 7},
                "project_id": "project-phase2",
            },
            declared_risk=RiskLevel.LOW,
        ),
    )
    task = await application.tasks.submit(create)
    task = await application.tasks.execute(task.task_id)

    usages = application.store.list_task_skill_usages(task_id=task.task_id)
    assert len(usages) == 1
    assert usages[0]["runtime_skill_id"] == skill.runtime_skill_id
    assert usages[0]["verification_status"] == "verified_success"
    assert usages[0]["project_id"] == "project-phase2"
    assert usages[0]["input_sha256"] == hashlib.sha256(b'{"value":7}').hexdigest()
    assert application.store.list_task_skill_usages(project_id="missing") == []
    application.artifacts.refresh_if_due(force=True)
    linked = application.store.get_task_skill_usage(usages[0]["usage_id"])
    assert len(linked["artifact_ids"]) == 1

    async def failed_worker(*_args, **_kwargs):
        raise RuntimeError("observable worker failure")

    monkeypatch.setattr(application.worker, "execute", failed_worker)
    failed_goal = "Record a failed isolated runtime skill execution."
    failed_create = create.model_copy(
        update={
            "goal": failed_goal,
            "success_contract": create.success_contract.model_copy(
                update={"goal": failed_goal}
            ),
            "action": create.action.model_copy(
                update={"payload": {**create.action.payload, "input": {"value": 8}}}
            ),
        }
    )
    failed_task = await application.tasks.submit(failed_create)
    failed_task = await application.tasks.execute(failed_task.task_id)
    failed_usage = application.store.list_task_skill_usages(task_id=failed_task.task_id)
    assert failed_usage[0]["result_status"] == "failed"
    assert failed_usage[0]["verification_status"] != "pending"


def test_artifact_catalog_is_projection_not_duplicate_storage(application) -> None:
    image = application.settings.image_output_dir / "phase2.png"
    image.write_bytes(b"verified-image-bytes")
    before = len(application.store.list_tasks(limit=10_000))

    indexed = application.artifacts.refresh_if_due(force=True)
    page = application.artifacts.page(limit=10, offset=0, artifact_type="image")

    assert indexed >= 1
    assert page["projection_only"] is True
    assert page["items"][0]["local_path"] == str(image.resolve())
    assert page["items"][0]["content_sha256"] == hashlib.sha256(
        b"verified-image-bytes"
    ).hexdigest()
    assert len(application.store.list_tasks(limit=10_000)) == before


def test_archive_round_trip_offline_corruption_and_local_safety(
    application, tmp_path: Path
) -> None:
    source = application.settings.export_dir / "verified.txt"
    source.write_text("archive payload", encoding="utf-8")
    application.artifacts.refresh_if_due(force=True)
    artifact = application.artifacts.page(
        limit=10, offset=0, artifact_type="document"
    )["items"][0]

    application.settings.archive_root = tmp_path / "missing-nas"
    with pytest.raises(ArchiveOfflineError):
        application.archive.archive(artifact["artifact_id"])
    assert source.exists()

    application.settings.archive_root.mkdir()
    record = application.archive.archive(artifact["artifact_id"], remove_local=True)
    assert record["status"] == "verified"
    assert not source.exists()

    restored = application.archive.acquire(artifact["artifact_id"])
    assert restored.read_text(encoding="utf-8") == "archive payload"
    application.archive.release(artifact["artifact_id"])

    manifest_path = Path(record["archive_path"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = Path(record["archive_path"]) / "payload" / manifest["files"][0]["path"]
    payload.write_text("corrupt", encoding="utf-8")
    cache = application.store.get_cache_entry(artifact["artifact_id"])
    assert cache is not None
    shutil.rmtree(Path(str(cache["cache_path"])))
    application.store.delete_cache_entry(artifact["artifact_id"])
    with pytest.raises(ArchiveIntegrityError):
        application.archive.acquire(artifact["artifact_id"])


@pytest.mark.asyncio
async def test_library_blocks_immature_domain_then_versions_verified_book(
    application,
) -> None:
    blocked = application.library_authoring.create_domain(
        title="Immature Domain",
        description="Must not publish after one search.",
        knowledge_selector={"query": "nonexistent"},
    )
    report = application.library_authoring.evaluate(blocked["domain_id"])
    assert report["passed"] is False
    with pytest.raises(LibraryReadinessError):
        application.library_authoring.author(blocked["domain_id"])

    domain_label = "library-domain:phase-2-acceptance"
    for index in range(1, 6):
        evaluation = (
            "library-fixed-evaluation" if index <= 3 else "library-hidden-evaluation"
        )
        task = await application.tasks.submit(
            _safe_task(index, (domain_label, evaluation))
        )
        await application.tasks.execute(task.task_id)
    domain = application.library_authoring.create_domain(
        title="Phase 2 Acceptance",
        description="A deterministic test-only domain proving the readiness gate.",
        knowledge_selector={
            "capability_prefixes": ["python.safe_expression"],
            "required_capabilities": ["python.safe_expression"],
        },
        thresholds={
            "min_cards": 5,
            "min_chapters": 1,
            "min_relation_coverage": 0.8,
            "min_independent_source_ratio": 0.0,
            "min_applied_tasks": 5,
            "min_fixed_tests": 3,
            "min_hidden_tests": 2,
            "min_evaluation_score": 1.0,
        },
    )
    cards = domain["cards"]
    for left, right in zip(cards, cards[1:], strict=False):
        application.library_authoring.add_relation(
            source_knowledge_id=left["knowledge_id"],
            target_knowledge_id=right["knowledge_id"],
            relation_type="extension",
            rationale="Acceptance graph ordering.",
            evidence_ids=[],
            verified=True,
        )

    passed = application.library_authoring.evaluate(domain["domain_id"])
    assert passed["passed"] is True
    assert passed["metrics"]["hidden_test_count"] == 2
    first = application.library_authoring.author(domain["domain_id"])
    duplicate = application.library_authoring.author(domain["domain_id"])

    assert first["created"] is True
    assert duplicate["created"] is False
    assert Path(first["markdown_path"]).is_file()
    assert application.library_authoring.domain(domain["domain_id"])["status"] == "published"


def test_workspace_phase2_rest_api_exposes_real_state(application) -> None:
    artifact = application.settings.export_dir / "api-result.txt"
    artifact.write_text("API result", encoding="utf-8")
    application.artifacts.refresh_if_due(force=True)
    api = create_api(application=application)

    with TestClient(api) as client:
        results = client.get("/v1/workspace/results?artifact_type=document")
        assert results.status_code == 200
        assert results.json()["projection_only"] is True
        artifact_id = results.json()["items"][0]["artifact_id"]
        assert client.get(f"/v1/workspace/results/{artifact_id}").status_code == 200
        assert client.get(f"/v1/workspace/results/{artifact_id}/preview").text == "API result"

        archive = client.get("/v1/workspace/archive/status")
        assert archive.json()["state"] == "unconfigured"
        assert client.post(
            f"/v1/workspace/results/{artifact_id}/archive",
            json={"remove_local": False},
        ).status_code == 503

        domain = client.post(
            "/v1/workspace/library/domains",
            json={
                "title": "API Gate Domain",
                "description": "Prove immature domains remain blocked.",
                "knowledge_selector": {"query": "not-present"},
            },
        )
        assert domain.status_code == 200
        domain_id = domain.json()["domain_id"]
        evaluation = client.post(
            f"/v1/workspace/library/domains/{domain_id}/evaluate"
        )
        assert evaluation.json()["passed"] is False
        assert client.post(
            f"/v1/workspace/library/domains/{domain_id}/author",
            json={"reason": "Must stay blocked"},
        ).status_code == 409
