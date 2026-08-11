from __future__ import annotations

import json
from pathlib import Path

import pytest

from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import (
    GuidedSkillAcquisitionRequest,
    RuntimeSkillManifest,
    SkillAcceptanceExample,
)
from eck.services.project_lab_components.github_policy import GitHubCommandPolicy
from eck.services.skill_forge import SkillForgeService
from eck.services.tool_campaign_components.state import gates_complete


def _source(*, license_spdx: str = "MIT") -> dict[str, object]:
    return {
        "name": "example/useful-tool",
        "url": "https://github.com/example/useful-tool",
        "description": "A useful deterministic text utility.",
        "license": license_spdx,
        "commit_sha": "a" * 40,
        "default_branch": "main",
        "stars": 5000,
        "updated_at": "2026-08-01T00:00:00Z",
        "classification": "executable-pattern-library",
        "file_profile": {"markdown": 2, "code": 30},
        "readme_sha256": "b" * 64,
        "readme_excerpt": "Normalize text by trimming whitespace and converting it to lowercase.",
        "adaptation_allowed": True,
        "inspection": "Pinned source inspection.",
        "archived": False,
        "fork": False,
        "campaign_category": "developer-tools",
    }


def _request() -> GuidedSkillAcquisitionRequest:
    return GuidedSkillAcquisitionRequest(
        topic="Verified adaptation of example/useful-tool",
        objective="Normalize supplied text with deterministic lowercase and trim behavior.",
        requested_name="tool.example_useful_tool.12345678",
        source_urls=("https://github.com/example/useful-tool",),
        acceptance_examples=(
            SkillAcceptanceExample(payload={"text": " ECK "}, expected={"text": "eck"}),
            SkillAcceptanceExample(payload={"text": "Tool"}, expected={"text": "tool"}),
        ),
    )


def test_all_five_gates_are_required_before_counting() -> None:
    gates = {
        name: {"passed": True}
        for name in (
            "license",
            "security_scan",
            "docker_test",
            "objective_benchmark",
            "local_reproduction",
        )
    }

    assert gates_complete(gates) is True
    gates["local_reproduction"]["passed"] = False
    assert gates_complete(gates) is False


def test_campaign_license_policy_excludes_isc(application) -> None:
    assert application.tool_campaign._source_allowed(_source()) is True
    assert application.tool_campaign._source_allowed(_source(license_spdx="ISC")) is False


def test_campaign_rejects_fake_external_success_contracts(application) -> None:
    with pytest.raises(ValueError, match="external actions"):
        application.tool_campaign._validate_contract(
            "Automate form filling on a website.",
            (
                SkillAcceptanceExample(
                    operation="fill_form",
                    payload={"url": "https://example.com", "password": "not-a-real-secret"},
                    expected={"status": "success", "message": "Form submitted."},
                ),
                SkillAcceptanceExample(
                    operation="fill_form",
                    payload={"url": "https://example.org"},
                    expected={"status": "success", "message": "Form submitted."},
                ),
            ),
        )


def test_campaign_rejects_peripheral_readme_features(application) -> None:
    assert application.tool_campaign._objective_relevance(
        "Normalize agent workflow security rules.",
        "Agent workflow skills, memory, and security optimization.",
    ) == ("agent", "security", "workflow")
    with pytest.raises(ValueError, match="peripheral README detail"):
        application.tool_campaign._objective_relevance(
            "Normalize and rank language options.",
            "Agent workflow skills, memory, and security optimization.",
        )


def test_autonomous_github_policy_is_fail_closed() -> None:
    GitHubCommandPolicy.validate(
        ["gh", "auth", "token", "--hostname", "github.com", "--user", "eck"]
    )
    GitHubCommandPolicy.validate(["gh", "api", "user", "--jq", ".login"])
    GitHubCommandPolicy.validate(["gh", "repo", "create", "eck/example", "--private"])

    with pytest.raises(RuntimeError, match="account-safety allowlist"):
        GitHubCommandPolicy.validate(["gh", "auth", "login"])
    with pytest.raises(RuntimeError, match="account-safety allowlist"):
        GitHubCommandPolicy.validate(["gh", "repo", "delete", "eck/example"])
    with pytest.raises(RuntimeError, match="account-safety allowlist"):
        GitHubCommandPolicy.validate(["gh", "api", "user/billing"])


@pytest.mark.asyncio
async def test_campaign_counts_only_packaged_locally_reproduced_skill(
    application,
    monkeypatch,
) -> None:
    application.settings.network_enabled = True
    source = _source()
    request = _request()
    source_dir = (
        application.forge.generated_root
        / str(request.requested_name)
        / "0.1.0"
    )
    source_dir.mkdir(parents=True)
    manifest = RuntimeSkillManifest(
        name=str(request.requested_name),
        version="0.1.0",
        description=request.objective,
        category="developer-tools",
        operations=("execute",),
        generated=True,
    )
    source_dir.joinpath("manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    source_dir.joinpath("skill.py").write_text(
        "def execute(operation, payload, context):\n"
        "    return {'text': str(payload.get('text', '')).strip().lower()}\n",
        encoding="utf-8",
    )
    source_dir.joinpath("test_skill.py").write_text(
        SkillForgeService._acceptance_tests(request.acceptance_examples),
        encoding="utf-8",
    )
    source_dir.joinpath("provenance.json").write_text(
        json.dumps({"sources": [source]}),
        encoding="utf-8",
    )
    skill = application.store.add_runtime_skill(
        manifest,
        source_dir=str(source_dir),
        source="eck-generated",
        status=RuntimeSkillStatus.ACTIVE,
        test_report={
            "success": True,
            "canary": {"passed": True, "completed_replays": 2},
        },
    )

    async def worker_ready(*, force: bool = False):
        return {"success": True, "force": force}

    async def discover(**kwargs):
        assert kwargs["cursor"] == 0
        return {
            "name": source["name"],
            "url": source["url"],
            "description": source["description"],
            "stars": source["stars"],
            "category": "developer-tools",
            "query": "test",
            "query_page": 1,
        }

    async def inspect(url: str):
        assert url == source["url"]
        return dict(source)

    async def plan(candidate_source):
        assert candidate_source["commit_sha"] == "a" * 40
        return request

    async def review(candidate_source, candidate_request):
        assert candidate_request == request
        return {
            "approved": True,
            "reason": "Examples match the documented normalization behavior.",
            "evidence": ["README describes trim and lowercase behavior."],
            "model": "review-test",
        }

    async def acquire(candidate_request, sources):
        assert candidate_request == request
        assert sources[0]["license"] == "MIT"
        return {
            "status": "guided_skill_activated",
            "runtime_skill": skill.model_dump(mode="json"),
        }

    async def reproduce(candidate):
        assert candidate.runtime_skill_id == skill.runtime_skill_id
        return {"success": True, "detail": "2 passed", "test_output": "2 passed"}

    async def publish(**kwargs):
        assert kwargs["name"] == "eck-agent-toolkit"
        return {
            "published": False,
            "deferred": True,
            "detail": "Test credential store intentionally unavailable.",
        }

    monkeypatch.setattr(application.forge, "ensure_worker_image", worker_ready)
    monkeypatch.setattr(application.tool_campaign.discovery, "discover", discover)
    monkeypatch.setattr(application.skill_bridge, "inspect_github_repository", inspect)
    monkeypatch.setattr(application.tool_campaign, "_plan_contract", plan)
    monkeypatch.setattr(application.tool_campaign, "_review_contract", review)
    monkeypatch.setattr(application.skill_bridge, "acquire_inspected", acquire)
    monkeypatch.setattr(application.worker, "validate", reproduce)
    monkeypatch.setattr(application.project_lab, "publish_directory", publish)

    result = await application.tool_campaign.run_once(force=True)

    assert result["status"] == "accepted"
    status = application.tool_campaign.status()
    assert status["accepted_count"] == 1
    assert status["remaining_count"] == 99
    catalog_path = Path(status["repository"]["workspace"]) / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["version"] == "0.1.0"
    assert catalog["entries"][0]["source"]["commit_sha"] == "a" * 40
    archives = list(catalog_path.parent.glob("evolution-packs/**/*.zip"))
    assert len(archives) == 1
    assert catalog["entries"][0]["evolution_pack"]["verification"]["valid"] is True

    application.settings.tool_campaign_state_path.unlink()
    recovered = application.tool_campaign.status()
    assert recovered["accepted_count"] == 1
    assert recovered["recent_candidates"][-1]["reason"] == (
        "recovered_from_verified_toolkit_catalog"
    )

    application.tool_campaign.catalog.revoke(
        skill.runtime_skill_id,
        reason="A later relevance audit found a regression.",
    )
    revoked = application.tool_campaign.status()
    assert revoked["accepted_count"] == 0
    assert revoked["repository"]["revoked_count"] == 1
