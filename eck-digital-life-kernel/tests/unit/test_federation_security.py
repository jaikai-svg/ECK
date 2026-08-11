from __future__ import annotations

import json
from pathlib import Path

import pytest

from eck.domain.enums import BenchmarkSuite, MissionStatus
from eck.domain.models import BenchmarkRunCreate, MissionCreate
from eck.services.federation_registry import CosignBlobService


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_data_pack_builders_reject_unverified_or_empty_records(application) -> None:
    packs = application.federation.data_packs
    with pytest.raises(ValueError, match="At least one"):
        packs.build_knowledge(())
    with pytest.raises(ValueError, match="at most"):
        packs._unique_ids(tuple(str(index) for index in range(51)), label="run", maximum=50)

    running = application.store.begin_research_run(
        action_id="security-test",
        topic="unfinished research",
        seed_url=None,
    )
    with pytest.raises(ValueError, match="completed"):
        packs.build_knowledge((running,))

    empty = application.store.begin_research_run(
        action_id="security-test-empty",
        topic="empty research",
        seed_url=None,
    )
    application.store.complete_research_run(
        empty,
        status="completed",
        conclusion_status="inconclusive",
        conclusion="No evidence.",
        confidence=0.1,
        queries=[],
        source_snapshot_ids=[],
        metrics={},
        report={},
        claims=[],
        evidence_links=[],
    )
    with pytest.raises(ValueError, match="claims and traceable"):
        packs.build_knowledge((empty,))

    mission = application.store.create_mission(
        MissionCreate(
            title="Unapproved strategy",
            objective="Test strategy export gates.",
            completion_requirements="Human approval is mandatory.",
        )
    )
    with pytest.raises(ValueError, match="human-approved"):
        packs.build_strategy(mission.mission_id)
    with pytest.raises(ValueError, match="human-approved"):
        packs.build_distillation((mission.mission_id,))

    application.store.set_mission_status(
        mission.mission_id,
        MissionStatus.APPROVED,
        progress={},
    )
    with pytest.raises(ValueError, match="learning pattern"):
        packs.build_strategy(mission.mission_id)
    application.store.set_mission_status(
        mission.mission_id,
        MissionStatus.APPROVED,
        progress={"learning_pattern": {"project_type": "general"}},
    )
    with pytest.raises(ValueError, match="step graph"):
        packs.build_strategy(mission.mission_id)
    with pytest.raises(ValueError, match="No successful ReAct"):
        packs.build_distillation((mission.mission_id,))

    with pytest.raises(KeyError, match="Unknown benchmark"):
        packs.build_evaluation(("benchmark_missing",))
    hidden = application.store.add_benchmark_run(
        BenchmarkRunCreate(
            suite=BenchmarkSuite.REAL_TASKS,
            benchmark_version="hidden-v1",
            model="test",
            evaluator="test",
            score=0.5,
            sample_count=1,
            protocol={"hidden_answer_key": ["do-not-export"]},
        )
    )
    with pytest.raises(ValueError, match="hidden-test answers"):
        packs.build_evaluation((hidden.run_id,))


def test_receiver_validation_rejects_malformed_data_packs(application, tmp_path: Path) -> None:
    packs = application.federation.data_packs
    with pytest.raises(ValueError, match="Unsupported"):
        packs.reproduce("adapter_pack", tmp_path)

    knowledge = tmp_path / "knowledge"
    _write(
        knowledge / "knowledge.json",
        {
            "schema": "eck-knowledge-pack.v1",
            "runs": [
                {
                    "claims": [{}],
                    "sources": [{"url": "file:///private", "content_sha256": "x"}],
                }
            ],
        },
    )
    assert packs.reproduce("knowledge_pack", knowledge)["success"] is False
    value = json.loads((knowledge / "knowledge.json").read_text(encoding="utf-8"))
    value["runs"][0]["sources"][0] = {"url": "https://example.com", "content_sha256": ""}
    _write(knowledge / "knowledge.json", value)
    assert "content hash" in packs.reproduce("knowledge_pack", knowledge)["detail"]

    strategy = tmp_path / "strategy"
    _write(
        strategy / "strategy.json",
        {
            "schema": "eck-strategy-pack.v1",
            "step_graph": [{"step_key": "", "depends_on": []}],
        },
    )
    assert "duplicated" in packs.reproduce("strategy_pack", strategy)["detail"]
    _write(
        strategy / "strategy.json",
        {
            "schema": "eck-strategy-pack.v1",
            "step_graph": [{"step_key": "a", "depends_on": ["missing"]}],
        },
    )
    assert "unknown dependency" in packs.reproduce("strategy_pack", strategy)["detail"]
    _write(
        strategy / "strategy.json",
        {
            "schema": "eck-strategy-pack.v1",
            "step_graph": [
                {"step_key": "a", "depends_on": ["b"]},
                {"step_key": "b", "depends_on": ["a"]},
            ],
        },
    )
    assert "dependency cycle" in packs.reproduce("strategy_pack", strategy)["detail"]

    evaluation = tmp_path / "evaluation"
    _write(
        evaluation / "evaluation.json",
        {
            "schema": "eck-evaluation-pack.v1",
            "runs": [{"score": 2, "sample_count": 0, "protocol": {}}],
        },
    )
    assert "score or sample" in packs.reproduce("evaluation_pack", evaluation)["detail"]
    _write(
        evaluation / "evaluation.json",
        {
            "schema": "eck-evaluation-pack.v1",
            "runs": [
                {"score": 0.5, "sample_count": 1, "protocol": {"hidden_answers": [1]}}
            ],
        },
    )
    assert "hidden-test" in packs.reproduce("evaluation_pack", evaluation)["detail"]

    distillation = tmp_path / "distillation"
    _write(
        distillation / "metadata.json",
        {"schema": "eck-distillation-pack.v1", "examples": 2},
    )
    (distillation / "distillation.jsonl").write_text("{}\n", encoding="utf-8")
    assert "does not match" in packs.reproduce("distillation_pack", distillation)["detail"]
    example = {"instruction": "test", "outcome": "failed"}
    _write(
        distillation / "metadata.json",
        {"schema": "eck-distillation-pack.v1", "examples": 1},
    )
    (distillation / "distillation.jsonl").write_text(
        json.dumps({"example_sha256": packs._digest(example), **example}) + "\n",
        encoding="utf-8",
    )
    assert "successful trajectory" in packs.reproduce(
        "distillation_pack", distillation
    )["detail"]


def test_data_pack_public_projection_is_bounded_and_private_key_safe(application) -> None:
    packs = application.federation.data_packs
    projected = packs._public_value(
        {
            "password": "hidden",
            "safe": ["value", {"owner_setting": "hidden", "kept": object()}],
        }
    )
    assert "password" not in projected
    assert "owner_setting" not in projected["safe"][1]
    assert projected["safe"][1]["kept"].startswith("<object object")
    assert packs._public_value({"deep": {}}, depth=6) == "[depth-limited]"
    assert packs._string_list("not-a-list", maximum=2) == []
    assert packs._pack_name("knowledge", ["???"]).startswith("knowledge-verified-")


def test_evaluation_pack_requires_comparable_artifact_change_for_growth(application) -> None:
    runs = []
    for score, artifact in ((0.5, "1" * 64), (0.8, "2" * 64)):
        runs.append(
            application.store.add_benchmark_run(
                BenchmarkRunCreate(
                    suite=BenchmarkSuite.REAL_TASKS,
                    benchmark_version="growth-v1",
                    model="local-model",
                    model_artifact_hash=artifact,
                    evaluator="fixed-runner",
                    score=score,
                    sample_count=20,
                    protocol={"scope": "fixed-public", "repetitions": 2},
                )
            )
        )
    built = application.federation.data_packs.build_evaluation(
        tuple(run.run_id for run in runs)
    )
    payload = json.loads(built["payloads"]["evaluation.json"])

    assert payload["quality"]["improvement_verified"] is True
    assert payload["comparisons"][0]["score_delta"] == pytest.approx(0.3)
    assert payload["comparisons"][0]["model_artifact_changed"] is True


def test_cosign_fail_closed_paths_and_successful_key_verification(
    settings,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings.federation_cosign_enabled = False
    settings.federation_cosign_executable = None
    cosign = CosignBlobService(settings)
    assert cosign.status()["installed"] is False
    with pytest.raises(RuntimeError, match="not installed"):
        cosign.sign(tmp_path / "missing.zip")

    settings.federation_cosign_enabled = True
    archive = tmp_path / "pack.zip"
    archive.write_bytes(b"pack")
    assert "missing" in cosign.verify(archive)["detail"]
    cosign.bundle_path(archive).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cosign, "_executable", lambda: None)
    assert "not installed" in cosign.verify(archive)["detail"]
    monkeypatch.setattr(cosign, "_executable", lambda: "cosign")
    assert "trust" in cosign.verify(archive)["detail"]

    settings.federation_cosign_certificate_identity = "https://example.com/workflow"
    settings.federation_cosign_oidc_issuer = "https://issuer.example.com"
    monkeypatch.setattr(
        cosign,
        "_run",
        lambda command: {"returncode": 1, "detail": "identity mismatch"},
    )
    assert cosign.verify(archive)["verified"] is False

    key = tmp_path / "cosign.key"
    public_key = tmp_path / "cosign.pub"
    key.write_text("private", encoding="utf-8")
    public_key.write_text("public", encoding="utf-8")
    settings.federation_cosign_key_path = key
    settings.federation_cosign_public_key_path = public_key
    monkeypatch.setattr(
        cosign,
        "_run",
        lambda command: {"returncode": 0, "detail": "Verified OK"},
    )
    signed = cosign.sign(archive)
    assert signed["verified"] is True


@pytest.mark.asyncio
async def test_registry_rejects_invalid_reviews_and_publishes_public_index(
    application,
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = application.federation.capability_registry
    pack_id = "evolution-pack_" + "d" * 32
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"candidate")
    with pytest.raises(ValueError, match="hash-valid"):
        registry.submit(
            archive,
            verification={"valid": False},
            manifest={},
            signature_bundle=None,
        )
    candidate = registry.submit(
        archive,
        verification={
            "valid": True,
            "pack_id": pack_id,
            "pack_type": "knowledge_pack",
            "archive_sha256": "e" * 64,
            "signature_verified": False,
        },
        manifest={"reproductions": []},
        signature_bundle=None,
    )
    assert candidate["reviews"] == []
    with pytest.raises(ValueError, match="thresholds"):
        registry.admit(pack_id, signature_verified=False)

    review = {
        "pack_id": pack_id,
        "reviewer_node_sha256": "a" * 64,
        "verdict": "approve",
        "reproduction_success": True,
        "fixed_test_delta": 0.0,
        "hidden_test_regression": False,
        "permission_reviewed": True,
        "dependency_reviewed": True,
        "evidence_sha256": "b" * 64,
        "notes": "ok",
    }
    for field, value, message in (
        ("reviewer_node_sha256", "bad", "Reviewer node"),
        ("verdict", "maybe", "verdict"),
        ("evidence_sha256", "bad", "evidence"),
    ):
        invalid = {**review, field: value}
        invalid.pop("pack_id")
        with pytest.raises(ValueError, match=message):
            registry.add_review(pack_id, **invalid)

    async def published(**kwargs):
        assert kwargs["name"] == "eck-capability-registry"
        assert kwargs["visibility"] == "public"
        return {"published": True, "url": "https://github.com/eck/registry"}

    monkeypatch.setattr(application.project_lab, "publish_directory", published)
    assert (await registry.publish())["published"] is True
    assert registry.file_sha256(archive)
    with pytest.raises(ValueError, match="Invalid Evolution Pack"):
        registry.candidate("bad-pack")

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid Registry JSON"):
        registry._read_json(invalid_json)
