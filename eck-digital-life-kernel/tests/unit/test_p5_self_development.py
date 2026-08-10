from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eck.app import build_application
from eck.brain.base import BrainHealth, BrainResponse
from eck.domain.models import DevelopmentProjectRequest
from eck.services import project_lab as project_lab_module


def test_p5_learning_portfolio_must_total_one_hundred(settings) -> None:
    payload = settings.model_dump()
    payload.update(
        {
            "p5_self_development_percent": 50,
            "p5_ai_research_percent": 30,
            "p5_foundation_percent": 15,
            "p5_exploration_percent": 10,
        }
    )

    with pytest.raises(ValidationError, match="P5 learning portfolio"):
        type(settings)(**payload)


@pytest.mark.asyncio
async def test_verified_project_is_isolated_disclosed_and_deferred_without_publisher(
    settings,
    monkeypatch,
) -> None:
    configured = settings.model_copy(
        update={
            "github_publish_enabled": False,
            "github_auto_publish_verified_projects": True,
            "autonomous_project_draft_attempts": 2,
        }
    )
    application = build_application(configured)
    drafts = iter(
        [
            {
                "name": "physics-engine-lab",
                "summary": "An incomplete first draft.",
                "files": [
                    {
                        "path": "physics_engine.py",
                        "content": "def position(x, velocity, seconds): return x\n",
                    }
                ],
            },
            {
                "name": "physics-engine-lab",
                "summary": "A deterministic one-dimensional motion simulator.",
                "files": [
                    {
                        "path": "physics_engine.py",
                        "content": (
                            "def physics_motion_position(x, velocity, seconds):\n"
                            "    return x + velocity * seconds\n"
                        ),
                    },
                    {
                        "path": "tests/test_physics_engine.py",
                        "content": (
                            "from physics_engine import physics_motion_position\n\n"
                            "def test_position():\n"
                            "    assert physics_motion_position(1, 2, 3) == 7\n"
                            "    assert physics_motion_position(0, -1, 2) == -2\n"
                        ),
                    },
                ],
            },
        ]
    )

    async def image_available() -> bool:
        return True

    async def chat(*args, **kwargs) -> BrainResponse:
        del args, kwargs
        return BrainResponse(
            content=json.dumps(next(drafts)),
            model="coder-test",
            raw={},
        )

    async def validate(source_dir: Path) -> dict[str, object]:
        assert (source_dir / "physics_engine.py").is_file()
        return {
            "success": True,
            "test_command": ["python", "-m", "pytest", "-q"],
            "returncode": 0,
            "output_tail": "1 passed",
            "isolated": True,
            "network": "none",
        }

    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.coder_brain, "chat", chat)
    monkeypatch.setattr(application.project_lab, "_validate_in_docker", validate)

    project = await application.project_lab.create(
        DevelopmentProjectRequest(
            objective=(
                "Create a deterministic local physics simulation with externally testable motion."
            ),
            publish_when_verified=True,
        )
    )

    assert project["status"] == "verified"
    assert len(project["validation"]["draft_attempts"]) == 2
    assert project["validation"]["draft_attempts"][0]["success"] is False
    assert project["github"]["deferred"] is True
    assert project["source_sha256"]
    readme = Path(project["source_dir"], "README.md").read_text(encoding="utf-8")
    assert "AI/ECK Autonomous Project" in readme
    assert application.project_lab.list_projects()[0]["project_id"] == project["project_id"]


def test_project_lab_rejects_unsafe_paths(application) -> None:
    with pytest.raises(ValueError, match="Unsafe autonomous project path"):
        application.project_lab._safe_relative_path("../host-secret.txt")


def test_project_lab_rejects_embedded_credentials(application, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.py").write_text(
        'api_key = "this-is-a-secret-value"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Potential secret"):
        application.project_lab._scan_secrets(source)


def test_project_research_evidence_is_bounded(application) -> None:
    evidence = application.project_lab._compact_research_evidence(
        {
            "run_id": "research-run-unit",
            "topic": "T" * 1000,
            "conclusion": "C" * 2000,
            "claims": [
                {"claim": "claim" * 300, "status": "supported", "confidence": 0.9}
                for _ in range(8)
            ],
            "sources": [
                {
                    "canonical_url": "https://example.com/" + "a" * 1000,
                    "title": "title" * 200,
                    "source_domain": "example.com",
                    "published_at": "2026-08-09",
                }
                for _ in range(8)
            ],
        }
    )

    assert len(evidence["topic"]) == 300
    assert len(evidence["conclusion"]) == 600
    assert len(evidence["claims"]) == 3
    assert len(evidence["sources"]) == 3
    assert len(json.dumps(evidence)) < 6000
    previous = application.project_lab._compact_previous_draft(
        {
            "name": "unit",
            "summary": "summary",
            "files": [{"path": "module.py", "content": "x" * 9000}],
        }
    )
    assert previous is not None
    assert len(previous["files"][0]["content"]) == 4000
    assert application.project_lab._plain_code("```python\nVALUE = 1\n```") == "VALUE = 1\n"


def test_project_quality_gate_rejects_simulated_success(application, tmp_path: Path) -> None:
    source = tmp_path / "simulated"
    tests = source / "tests"
    tests.mkdir(parents=True)
    (source / "experiment.py").write_text(
        "import random\n\ndef optimize():\n    return 'Optimization successful'\n",
        encoding="utf-8",
    )
    (tests / "test_experiment.py").write_text(
        "from experiment import optimize\n\ndef test_optimize():\n"
        "    assert isinstance(optimize(), str)\n",
        encoding="utf-8",
    )

    quality = application.project_lab._static_quality_gate(source)

    assert quality["success"] is False
    assert any("Non-deterministic" in issue for issue in quality["issues"])
    assert any("simulated success" in issue.casefold() for issue in quality["issues"])


def test_project_quality_gate_rejects_external_dependencies(application, tmp_path: Path) -> None:
    source = tmp_path / "external"
    tests = source / "tests"
    tests.mkdir(parents=True)
    (source / "experiment.py").write_text(
        "import scipy\n\ndef calculate(value):\n    return value * 2\n",
        encoding="utf-8",
    )
    (tests / "test_experiment.py").write_text(
        "from experiment import calculate\n\ndef test_calculate():\n"
        "    assert calculate(2) == 4\n    assert calculate(-1) == -2\n",
        encoding="utf-8",
    )

    quality = application.project_lab._static_quality_gate(source)

    assert quality["success"] is False
    assert any("Non-standard dependency" in issue for issue in quality["issues"])


def test_project_quality_gate_rejects_undefined_names_and_mocked_tests(
    application, tmp_path: Path
) -> None:
    source = tmp_path / "mocked"
    tests = source / "tests"
    tests.mkdir(parents=True)
    (source / "experiment.py").write_text(
        "def calculate(value):\n    return missing_engine.run(value)\n",
        encoding="utf-8",
    )
    (tests / "test_experiment.py").write_text(
        "from unittest.mock import patch\nfrom experiment import calculate\n\n"
        "def test_calculate():\n    with patch('experiment.calculate') as mocked:\n"
        "        mocked.return_value = 4\n        assert calculate(2) == 4\n"
        "        assert calculate(3) == 4\n",
        encoding="utf-8",
    )

    quality = application.project_lab._static_quality_gate(source)

    assert quality["success"] is False
    assert any("Undefined names" in issue for issue in quality["issues"])
    assert any("may not mock" in issue for issue in quality["issues"])


def test_project_quality_gate_rejects_irrelevant_code(application, tmp_path: Path) -> None:
    source = tmp_path / "irrelevant"
    tests = source / "tests"
    tests.mkdir(parents=True)
    (source / "main.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    (tests / "test_main.py").write_text(
        "from main import add\n\ndef test_add():\n"
        "    assert add(1, 2) == 3\n    assert add(-1, 1) == 0\n",
        encoding="utf-8",
    )

    quality = application.project_lab._static_quality_gate(
        source,
        objective="Lead topic: test coverage regression and failure localization",
    )

    assert quality["success"] is False
    assert any("not relevant" in issue for issue in quality["issues"])


@pytest.mark.asyncio
async def test_project_lab_status_and_scheduler_gates(application, monkeypatch) -> None:
    service = application.project_lab
    malformed = service.root / ("project_" + "f" * 32) / "manifest.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "github_status",
        lambda **kwargs: {"ready": False, "detail": "login required"},
    )

    status = await service.status()

    assert status["project_count"] == 0
    assert status["github"]["ready"] is False
    with pytest.raises(KeyError, match="Unknown autonomous project"):
        service.get_project("project_" + "e" * 32)

    service.settings = service.settings.model_copy(
        update={"autonomous_project_lab_enabled": False}
    )
    assert (await service.run_if_needed())["status"] == "disabled"

    service.settings = service.settings.model_copy(
        update={"autonomous_project_lab_enabled": True}
    )
    monkeypatch.setattr(service, "_cycle_due", lambda: False)
    assert (await service.run_if_needed())["status"] == "waiting_interval"

    monkeypatch.setattr(service, "_cycle_due", lambda: True)

    async def unavailable_worker() -> bool:
        return False

    monkeypatch.setattr(application.worker, "image_available", unavailable_worker)
    assert (await service.run_if_needed(force=True))["status"] == "waiting_worker"

    async def available_worker() -> bool:
        return True

    async def unavailable_coder() -> BrainHealth:
        return BrainHealth(provider="unit", available=False, detail="coder unavailable")

    monkeypatch.setattr(application.worker, "image_available", available_worker)
    monkeypatch.setattr(application.coder_brain, "health", unavailable_coder)
    assert (await service.run_if_needed(force=True))["status"] == "waiting_coder"

    async def available_coder() -> BrainHealth:
        return BrainHealth(provider="unit", available=True, model="coder-test")

    monkeypatch.setattr(application.coder_brain, "health", available_coder)
    monkeypatch.setattr(service, "_eligible_research", lambda: [])
    assert (await service.run_if_needed(force=True))["status"] == "waiting_research"

    research = [
        {"run_id": f"run-{index}", "topic": "verified software engineering"}
        for index in range(service.settings.autonomous_project_min_research_runs)
    ]

    async def create(request: DevelopmentProjectRequest) -> dict[str, str]:
        assert len(request.research_run_ids) == 1
        return {"project_id": "project_" + "d" * 32, "status": "verified"}

    monkeypatch.setattr(service, "_eligible_research", lambda: research)
    monkeypatch.setattr(service, "create", create)
    cycle = await service.run_if_needed(force=True)
    assert cycle["status"] == "verified"
    assert cycle["project_id"] == "project_" + "d" * 32


@pytest.mark.asyncio
async def test_verified_project_publishes_only_after_ready_check(application, monkeypatch) -> None:
    service = application.project_lab
    project_id = "project_" + "a" * 32
    project_dir = service._project_dir(project_id)
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = {
        "project_id": project_id,
        "name": "verified-project",
        "status": "draft",
        "source_dir": str(source_dir),
        "visibility": "private",
        "github": {"published": False},
    }
    service._write_manifest(project_dir, manifest)

    with pytest.raises(RuntimeError, match="Only verified"):
        await service.publish(project_id)

    manifest["status"] = "verified"
    service._write_manifest(project_dir, manifest)
    monkeypatch.setattr(
        service,
        "github_status",
        lambda **kwargs: {
            "ready": True,
            "authenticated": True,
            "account": "eck-unit",
            "executable": "gh",
        },
    )
    monkeypatch.setattr(service, "_scan_secrets", lambda source: None)
    monkeypatch.setattr(service, "_initialize_git", lambda source: None)
    monkeypatch.setattr(service, "_github_token", lambda executable, account: "unit-token")

    async def run_process(*args, **kwargs) -> dict[str, object]:
        del args
        assert kwargs["env"]["GH_TOKEN"] == "unit-token"
        return {"returncode": 0, "output_tail": "pushed"}

    monkeypatch.setattr(service, "_run_process", run_process)
    published = await service.publish(project_id)

    assert published["status"] == "published"
    assert published["github"]["repository"] == "eck-unit/verified-project"
    assert published["github"]["url"].endswith("eck-unit/verified-project")


@pytest.mark.asyncio
async def test_existing_git_history_creates_missing_named_remote(
    application,
    monkeypatch,
) -> None:
    service = application.project_lab
    source_dir = service.root / "existing-mission"
    source_dir.joinpath(".git").mkdir(parents=True)
    source_dir.joinpath("index.html").write_text("<main>Travel</main>", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "github_status",
        lambda **kwargs: {
            "ready": True,
            "authenticated": True,
            "account": "eck-unit",
            "executable": "gh",
        },
    )
    monkeypatch.setattr(service, "_scan_secrets", lambda source: None)
    monkeypatch.setattr(service, "_github_token", lambda executable, account: "unit-token")
    commands = []

    async def run_process(command, **kwargs):
        assert kwargs["env"]["GH_TOKEN"] == "unit-token"
        commands.append(command)
        if command[1:3] == ["repo", "view"]:
            return {"returncode": 1, "output_tail": "repository not found"}
        return {"returncode": 0, "output_tail": "repository created"}

    async def publish_existing_directory(**kwargs):
        assert kwargs["repository"] == "eck-unit/travel-task-0003"
        return {"returncode": 0, "output_tail": "history pushed"}

    monkeypatch.setattr(service, "_run_process", run_process)
    monkeypatch.setattr(
        service,
        "_publish_existing_directory",
        publish_existing_directory,
    )

    result = await service.publish_directory(
        name="travel-task-0003",
        source_dir=source_dir,
        visibility="private",
    )

    assert result["published"] is True
    assert result["repository"] == "eck-unit/travel-task-0003"
    assert commands[0][1:3] == ["repo", "view"]
    assert commands[1] == ["gh", "repo", "create", "eck-unit/travel-task-0003", "--private"]


@pytest.mark.asyncio
async def test_project_repairs_preserve_last_contract_valid_candidate(
    settings, monkeypatch
) -> None:
    application = build_application(
        settings.model_copy(update={"autonomous_project_draft_attempts": 3})
    )
    drafts = iter(
        [
            {
                "name": "bounded-candidate",
                "summary": "A contract-valid candidate that still needs quality repair.",
                "files": [
                    {"path": "module.py", "content": "def value():\n    return 1\n"},
                    {
                        "path": "tests/test_module.py",
                        "content": "def test_value():\n    assert 1 == 1\n    assert 2 == 2\n",
                    },
                ],
            },
            {"name": "worse", "summary": "missing tests", "files": []},
            {"name": "worse", "summary": "still missing tests", "files": []},
        ]
    )

    async def image_available() -> bool:
        return True

    async def chat(*args, **kwargs) -> BrainResponse:
        del args, kwargs
        return BrainResponse(content=json.dumps(next(drafts)), model="coder-test", raw={})

    async def rejected_split(*args, **kwargs) -> dict[str, object]:
        del args, kwargs
        return {"name": "rejected", "summary": "invalid", "files": []}

    monkeypatch.setattr(application.worker, "image_available", image_available)
    monkeypatch.setattr(application.coder_brain, "chat", chat)
    monkeypatch.setattr(application.project_lab, "_draft_split", rejected_split)
    monkeypatch.setattr(
        application.project_lab,
        "_static_quality_gate",
        lambda source, **kwargs: {
            "success": False,
            "issues": ["quality repair required"],
            "source_functions": 1,
            "behavioral_assertions": 2,
        },
    )

    project = await application.project_lab.create(
        DevelopmentProjectRequest(
            objective="Preserve the best bounded candidate during repair.",
            publish_when_verified=False,
        )
    )

    assert project["status"] == "failed"
    assert project["name"] == "bounded-candidate"
    assert Path(project["source_dir"], "module.py").is_file()
    assert len(project["validation"]["draft_attempts"]) == 4
    assert project["validation"]["draft_attempts"][-1]["attempt"] == "split-file"


def test_github_status_covers_install_auth_and_account_checks(application, monkeypatch) -> None:
    service = application.project_lab
    monkeypatch.setattr(service, "_gh_executable", lambda: None)
    assert service.github_status(force=True)["detail"] == "GitHub CLI is not installed."

    monkeypatch.setattr(service, "_gh_executable", lambda: "gh")
    monkeypatch.setattr(
        project_lab_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="login"),
    )
    unauthenticated = service.github_status(force=True)
    assert unauthenticated["authenticated"] is False
    assert service.github_status() is unauthenticated

    monkeypatch.setattr(
        project_lab_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="existing-account\n", stderr=""
        ),
    )
    unconfigured = service.github_status(force=True)
    assert unconfigured["ready"] is False
    assert "dedicated ECK account" in unconfigured["detail"]

    results = iter(
        [
            SimpleNamespace(returncode=0, stdout="unit-token\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="different-account\n", stderr=""),
        ]
    )
    monkeypatch.setattr(project_lab_module.subprocess, "run", lambda *args, **kwargs: next(results))
    service.settings = service.settings.model_copy(update={"github_account": "expected-account"})
    mismatch = service.github_status(force=True)
    assert mismatch["ready"] is False
    assert "expected-account" in mismatch["detail"]

    results = iter(
        [
            SimpleNamespace(returncode=0, stdout="unit-token\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="expected-account\n", stderr=""),
        ]
    )
    monkeypatch.setattr(project_lab_module.subprocess, "run", lambda *args, **kwargs: next(results))
    ready = service.github_status(force=True)
    assert ready["ready"] is True
    assert ready["account"] == "expected-account"


def test_github_environment_routes_only_the_dedicated_account_token(application) -> None:
    environment = application.project_lab._github_environment("dedicated-token")

    assert environment["GH_TOKEN"] == "dedicated-token"
    assert environment["GH_HOST"] == "github.com"


@pytest.mark.asyncio
async def test_project_validation_process_and_helpers(
    application, monkeypatch, tmp_path: Path
) -> None:
    service = application.project_lab
    source = tmp_path / "project"
    source.mkdir()

    async def unavailable_image() -> dict[str, object]:
        return {"available": False, "detail": "missing image"}

    monkeypatch.setattr(application.worker, "image_status", unavailable_image)
    assert (await service._validate_in_docker(source))["success"] is False

    async def available_image() -> dict[str, object]:
        return {"available": True, "executable": "docker"}

    async def passing_process(*args, **kwargs) -> dict[str, object]:
        del args, kwargs
        return {"returncode": 0, "output_tail": "2 passed"}

    monkeypatch.setattr(application.worker, "image_status", available_image)
    monkeypatch.setattr(service, "_run_process", passing_process)
    validation = await service._validate_in_docker(source)
    assert validation["success"] is True
    assert validation["network"] == "none"

    result = await service.__class__._run_process(
        [sys.executable, "-c", "print('ready')"], cwd=source, timeout=10
    )
    assert result["returncode"] == 0
    assert "ready" in result["output_tail"]
    timed_out = await service.__class__._run_process(
        [sys.executable, "-c", "import time; time.sleep(1)"], cwd=source, timeout=0.01
    )
    assert timed_out["returncode"] is None

    git_source = tmp_path / "git-project"
    git_source.mkdir()
    (git_source / "README.md").write_text("# Verified\n", encoding="utf-8")
    service._initialize_git(git_source)
    assert (git_source / ".git").is_dir()
    service._initialize_git(git_source)

    files = service._validate_files(
        [
            {"path": "requirements.txt", "content": ""},
            {"path": "module.py", "content": "VALUE = 1\n"},
            {"path": "tests/test_module.py", "content": "def test_value(): assert 1 == 1\n"},
        ]
    )
    service._write_project_files(source, files)
    assert not (source / "requirements.txt").exists()
    assert len(service._source_hash(source)) == 64
    assert service._safe_project_name("***", "project_" + "b" * 32).startswith("eck-project-")
    assert service._safe_project_name("ab", "project_" + "b" * 32) == "ab-eck"
    assert service._json_object('prefix {"ready": true} suffix') == {"ready": True}
    assert service._json_object("not json") == {}


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ([], "no files"),
        (["bad"], "must be an object"),
        ([{"path": "module.py", "content": ""}], "Empty autonomous"),
        ([{"path": "asset.exe", "content": "binary"}], "Unsupported"),
        ([{"path": "module.py", "content": "x = 1"}], "pytest tests"),
        (
            [{"path": "tests/test_only.py", "content": "def test_ok(): pass"}],
            "executable Python source",
        ),
    ],
)
def test_project_file_contract_rejections(application, files, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        application.project_lab._validate_files(files)


@pytest.mark.asyncio
async def test_split_file_draft_and_ast_guided_test_repair(application, monkeypatch) -> None:
    responses = iter(
        [
            BrainResponse(
                content=(
                    "def coverage_regression_delta(previous, current):\n"
                    "    return round(current - previous, 3)\n"
                ),
                model="coder-test",
                raw={},
            ),
            BrainResponse(
                content=(
                    "from experiment import coverage_regression_delta\n\n"
                    "def test_delta():\n"
                    "    assert coverage_regression_delta(80.0, 82.5) == 2.5\n"
                    "    assert coverage_regression_delta(90.0, 88.0) == -2.0\n"
                ),
                model="coder-test",
                raw={},
            ),
            BrainResponse(
                content=(
                    "```python\n"
                    "from experiment import coverage_regression_delta\n\n"
                    "def test_repaired_delta():\n"
                    "    assert coverage_regression_delta(10.0, 11.0) == 1.0\n"
                    "    assert coverage_regression_delta(5.0, 2.0) == -3.0\n"
                    "```"
                ),
                model="coder-test",
                raw={},
            ),
        ]
    )

    async def chat(*args, **kwargs) -> BrainResponse:
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(application.coder_brain, "chat", chat)
    request = DevelopmentProjectRequest(
        objective="Lead topic: test coverage regression and failure localization",
        publish_when_verified=False,
    )

    draft = await application.project_lab._draft_split(
        request,
        [],
        feedback="The one-shot contract failed.",
        previous_draft=None,
    )
    repaired = await application.project_lab._repair_split_tests(
        request,
        draft["files"][0]["content"],
        failure="The first test imported an unknown function.",
    )

    assert draft["name"].startswith("eck-experiment-")
    assert draft["files"][0]["path"] == "experiment.py"
    assert draft["files"][1]["path"] == "tests/test_experiment.py"
    assert "test_repaired_delta" in repaired


@pytest.mark.asyncio
async def test_project_post_audit_downgrades_irrelevant_verified_result(
    application, monkeypatch
) -> None:
    service = application.project_lab
    project_id = "project_" + "c" * 32
    project_dir = service._project_dir(project_id)
    source_dir = project_dir / "source"
    tests = source_dir / "tests"
    tests.mkdir(parents=True)
    (source_dir / "main.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    (tests / "test_main.py").write_text(
        "from main import add\n\ndef test_add():\n"
        "    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n",
        encoding="utf-8",
    )
    service._write_manifest(
        project_dir,
        {
            "project_id": project_id,
            "name": "irrelevant-verified",
            "objective": "Lead topic: memory retrieval and experience reuse",
            "status": "verified",
            "source_dir": str(source_dir),
            "validation": {"success": True},
            "created_at": "2026-08-09T00:00:00+00:00",
        },
    )
    service._write_json(
        service.state_path,
        {"project_id": project_id, "status": "verified"},
    )
    monkeypatch.setattr(
        service,
        "github_status",
        lambda **kwargs: {"ready": False, "detail": "not configured"},
    )

    status = await service.status()
    rejected = service.get_project(project_id)

    assert status["verified_count"] == 0
    assert rejected["status"] == "quality_rejected"
    assert rejected["validation"]["isolated_test_success"] is True
    assert rejected["validation"]["success"] is False
    assert service._read_json(service.state_path)["status"] == "quality_rejected"
