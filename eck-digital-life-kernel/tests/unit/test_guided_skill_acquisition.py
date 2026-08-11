from __future__ import annotations

import json

import pytest

from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import (
    GuidedSkillAcquisitionRequest,
    RuntimeSkillManifest,
    SkillAcceptanceExample,
    SkillForgeRequest,
)
from eck.services.skill_forge import SkillForgeService


def test_generated_entrypoint_is_normalized_without_self_import() -> None:
    code = SkillForgeService._ensure_execute_entrypoint(
        "from skill import execute\n\n"
        "def review(operation, payload, context):\n"
        "    return {'status': 'ok'}\n"
    )

    assert "from skill import execute" not in code
    assert "def execute(operation, payload, context):" in code
    assert "return review(operation, payload, context)" in code


def test_contradictory_generated_tests_are_rejected_before_worker() -> None:
    tests = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('payload, expected', ["
        "({'value': 1}, {'status': 'ok'}),"
        "({'value': 1}, {'status': 'error'})])\n"
        "def test_execute(payload, expected):\n"
        "    assert execute('run', payload, {}) == expected\n"
    )

    with pytest.raises(ValueError, match="Contradictory parameterized tests"):
        SkillForgeService._validate_test_consistency(tests)


def test_undeclared_generated_dependency_is_rejected_before_worker() -> None:
    with pytest.raises(ValueError, match="undeclared third-party modules"):
        SkillForgeService._dependency_scan("import invented_package\n", ())

    SkillForgeService._dependency_scan("import json\n", ())


def test_operator_examples_become_immutable_acceptance_tests() -> None:
    tests = SkillForgeService._acceptance_tests(
        (
            SkillAcceptanceExample(
                payload={"text": "ECK"},
                expected={"length": 3},
            ),
        )
    )

    assert SkillForgeService._is_acceptance_oracle(tests)
    assert "from skill import execute" in tests
    assert '"length": 3' in tests
    assert SkillForgeService._acceptance_cases_from_tests(tests) == [
        {
            "operation": "execute",
            "payload": {"text": "ECK"},
            "expected": {"length": 3},
            "context": {},
        }
    ]


def test_guided_manifest_defaults_are_deterministic(application) -> None:
    bridge = application.skill_bridge

    assert bridge._guided_name("Claim Evidence Audit") == "guided.claim_evidence_audit"
    assert bridge._guided_name("證據稽核").startswith("guided.")
    assert bridge._guided_permissions("Fetch a public web page and export file") == (
        "network:public",
        "artifact:write",
    )

    request = GuidedSkillAcquisitionRequest(
        topic="text length",
        objective="Return the observable length of the supplied text value.",
        acceptance_examples=(
            SkillAcceptanceExample(payload={"text": "ECK"}, expected={"length": 3}),
        ),
    )
    assert request.acceptance_examples[0].expected == {"length": 3}


@pytest.mark.asyncio
async def test_validation_restores_worker_image_before_running(application, monkeypatch) -> None:
    skill = application.store.list_runtime_skills(limit=1)[0]
    restored = False

    async def ensure_worker_image(*, force: bool = False):
        nonlocal restored
        restored = True
        return {"success": True}

    async def validate(candidate):
        assert restored
        return {"success": True, "detail": "passed", "test_output": "1 passed"}

    monkeypatch.setattr(application.forge, "ensure_worker_image", ensure_worker_image)
    monkeypatch.setattr(application.worker, "validate", validate)
    application.forge.settings.skill_canary_replays = 1

    result = await application.forge.validate_skill(skill.runtime_skill_id)

    assert result["status"] == "active"


@pytest.mark.asyncio
async def test_guided_acquisition_creates_provenance_and_counts_only_activation(
    application,
    monkeypatch,
    tmp_path,
) -> None:
    source_dir = tmp_path / "guided-skill"
    source_dir.mkdir()
    (source_dir / "skill.py").write_text("def execute(*args):\n    return {}\n", encoding="utf-8")
    (source_dir / "test_skill.py").write_text(
        "from skill import execute\n\ndef test_execute():\n    assert execute() == {}\n",
        encoding="utf-8",
    )
    manifest = RuntimeSkillManifest(
        name="review.frontend_quality",
        version="0.1.0",
        description="Review frontend quality with a deterministic rubric.",
        category="review",
        operations=("review",),
        generated=True,
    )
    active = application.store.add_runtime_skill(
        manifest,
        source_dir=str(source_dir),
        source="eck-generated",
        status=RuntimeSkillStatus.ACTIVE,
    )

    async def worker_ready(*, force: bool = False):
        return {"success": True, "force": force}

    async def proposal(request, sources):
        assert request.topic == "frontend agent review"
        assert sources[0]["source_id"] == "agency-agents"
        return (
            SkillForgeRequest(
                name="review.frontend_quality",
                objective="Review frontend quality with observable acceptance checks.",
                category="review",
                operations=("review",),
            ),
            "Adapt an attributed role rubric into an ECK-native executable review.",
        )

    async def forge(request):
        assert request.name == "review.frontend_quality"
        return active

    monkeypatch.setattr(application.forge, "ensure_worker_image", worker_ready)
    monkeypatch.setattr(application.skill_bridge, "_propose_guided", proposal)
    monkeypatch.setattr(application.forge, "forge", forge)

    result = await application.skill_bridge.acquire(
        GuidedSkillAcquisitionRequest(
            topic="frontend agent review",
            objective="Review a frontend and return evidence-specific corrections.",
        )
    )

    assert result["status"] == "guided_skill_activated"
    provenance = json.loads((source_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["sources"][0]["license"] == "MIT"
    assert provenance["policy"].startswith("Upstream content was treated as untrusted")


def test_guided_skill_api_is_exposed(application, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from eck.api.main import create_api

    async def acquire(request):
        return {"status": "guided_skill_activated", "topic": request.topic}

    monkeypatch.setattr(application.skill_bridge, "acquire", acquire)
    with TestClient(create_api(application=application)) as client:
        response = client.post(
            "/v1/learning/skills/acquire",
            json={
                "topic": "frontend review",
                "objective": "Return a structured frontend quality report with evidence.",
                "source_urls": [],
            },
        )

    assert response.status_code == 202
    assert response.json()["status"] == "guided_skill_activated"


def test_failed_skill_repair_api_is_exposed(application, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from eck.api.main import create_api

    candidate = application.store.list_runtime_skills(limit=1)[0]

    async def repair(runtime_skill_id):
        assert runtime_skill_id == candidate.runtime_skill_id
        return candidate

    monkeypatch.setattr(application.forge, "repair_failed_skill", repair)
    with TestClient(create_api(application=application)) as client:
        response = client.post(
            f"/v1/runtime/skills/{candidate.runtime_skill_id}/repair"
        )

    assert response.status_code == 202
    assert response.json()["runtime_skill_id"] == candidate.runtime_skill_id
