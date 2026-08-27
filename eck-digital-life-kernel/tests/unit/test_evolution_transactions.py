from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.events.bus import EventBus
from eck.services.evolution_policy import EvolutionProtectedSurfacePolicy
from eck.services.evolution_transaction import EvolutionTransactionService
from eck.storage.sqlite import SQLiteStore


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def _recovery_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "evolution-recovery.py"
    spec = importlib.util.spec_from_file_location("eck_evolution_recovery", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protected_policy_blocks_recovery_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    policy = EvolutionProtectedSurfacePolicy(root)

    with pytest.raises(ValueError, match="immutable recovery"):
        policy.assert_candidate_allowed(["src/eck/runtime/shutdown.py"])
    with pytest.raises(ValueError, match="immutable recovery"):
        policy.assert_candidate_allowed(["scripts/stop-eck.ps1"])

    rows = policy.assert_candidate_allowed(["src/eck/kernel/runtime.py", "src/eck/demo.py"])
    assert rows[0]["category"] == "owner_approval_required"
    assert rows[1]["category"] == "ordinary_structural_candidate"


def test_evaluation_assessment_distinguishes_improvement_and_non_regression() -> None:
    baseline = {"pass_rate": 0.0, "duration_seconds": 2.0}
    candidate = {"pass_rate": 1.0, "duration_seconds": 1.0}
    pack = {"minimum_speedup_percent": 0, "allow_non_regression": False}
    improved = EvolutionTransactionService._assess_evaluation(
        baseline, candidate, pack
    )
    assert improved["verdict"] == "verified_improvement"
    assert improved["eligible_for_human_approval"] is True

    baseline["pass_rate"] = 1.0
    unchanged = EvolutionTransactionService._assess_evaluation(
        baseline, candidate, pack
    )
    assert unchanged["verdict"] == "no_measurable_improvement"

    pack["allow_non_regression"] = True
    maintenance = EvolutionTransactionService._assess_evaluation(
        baseline, candidate, pack
    )
    assert maintenance["verdict"] == "verified_maintenance_non_regression"


@pytest.mark.asyncio
async def test_heldout_pack_is_hash_bound(application) -> None:
    service = application.evolution_transactions
    pack_dir = service.heldout_root / "candidate-correctness"
    pack_dir.mkdir(parents=True)
    test_path = pack_dir / "test_hidden.py"
    test_path.write_text("def test_hidden():\n    assert True\n", encoding="utf-8")

    pack = await service.register_heldout_pack(
        pack_id="candidate-correctness",
        description="Hidden correctness check created outside the candidate worktree.",
        test_files=("test_hidden.py",),
        change_kind="correctness",
        minimum_speedup_percent=0,
        allow_non_regression=False,
    )

    assert pack["pack_sha256"]
    assert service._verify_pack_files(pack) == (test_path.resolve(),)
    test_path.write_text("def test_hidden():\n    assert False\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after registration"):
        service._verify_pack_files(pack)


@pytest.mark.asyncio
async def test_exact_activation_restart_receipt_and_revert_rollback(
    settings,
    tmp_path: Path,
) -> None:
    git_root = tmp_path / "repository"
    project_root = git_root / "eck-digital-life-kernel"
    source = project_root / "src" / "eck" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(git_root, "init")
    _git(git_root, "add", ".")
    _git(
        git_root,
        "-c",
        "user.name=ECK Test",
        "-c",
        "user.email=eck-test@local",
        "commit",
        "-m",
        "baseline",
    )
    base_commit = _git(git_root, "rev-parse", "HEAD").strip()

    configured = settings.model_copy(
        update={
            "workspace_dir": tmp_path / "workspace",
            "database_path": tmp_path / "data" / "eck.db",
        }
    )
    configured.prepare_directories()
    assert configured.database_path is not None
    store = SQLiteStore(configured.database_path)
    store.initialize()
    events = EventBus(store)
    service = EvolutionTransactionService(
        configured,
        store,
        events,
        project_root=project_root,
    )

    candidate_id = new_id("core-candidate")
    checkout = tmp_path / "candidate-worktree"
    _git(git_root, "worktree", "add", "--detach", str(checkout), base_commit)
    candidate_project = checkout / "eck-digital-life-kernel"
    candidate_source = candidate_project / "src" / "eck" / "example.py"
    candidate_source.write_text("VALUE = 2\n", encoding="utf-8")
    _git(candidate_project, "add", "--", "src/eck/example.py")
    patch = _git(
        candidate_project,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
        "--",
        ".",
    )
    tree_sha = _git(candidate_project, "write-tree").strip()
    metadata = service.metadata_root / candidate_id
    metadata.mkdir(parents=True)
    patch_path = metadata / "candidate.patch"
    patch_path.write_text(patch, encoding="utf-8", newline="")
    manifest = {
        "schema_version": "eck-core-candidate.v1",
        "candidate_id": candidate_id,
        "objective": "Prove exact structural activation with a reversible value update.",
        "source_commit": base_commit,
        "source_tree_sha256": "a" * 64,
        "project_path": str(candidate_project),
        "changed_files": [{"path": "src/eck/example.py"}],
        "patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
        "candidate_tree_sha": tree_sha,
        "protected_paths": [],
        "status": "validated_awaiting_human",
        "validation": {"passed": True, "gates": []},
        "created_at": "2026-08-27T00:00:00+00:00",
        "updated_at": "2026-08-27T00:00:00+00:00",
    }
    (metadata / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    transaction = service.observe_candidate(manifest)
    evaluation = store.create_evolution_evaluation(
        {
            "transaction_id": transaction["transaction_id"],
            "pack_id": "hidden-correctness",
            "pack_sha256": "b" * 64,
            "baseline": {"pass_rate": 0.0},
            "candidate": {"pass_rate": 1.0},
            "result": {"eligible_for_human_approval": True},
            "verdict": "verified_improvement",
            "improvement_score": 1.0,
        }
    )
    store.update_evolution_transaction(
        transaction["transaction_id"],
        status="awaiting_human_approval",
    )

    approved = await service.approve(
        candidate_id,
        approved_by="owner",
        reason="The hidden correctness result is verified and the exact tree is approved.",
        confirmed_candidate_tree_sha=tree_sha,
    )
    assert approved["approval"]["evaluation_id"] == evaluation["evaluation_id"]

    activated = await service.activate(
        candidate_id,
        confirmed_candidate_tree_sha=tree_sha,
        reason="Activate the exact approved tree and require a startup receipt.",
    )
    assert activated["status"] == "restart_pending"
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
    receipts = await service.reconcile_startup(boot_count=2)
    assert receipts[0]["status"] == "verified"
    absorbed = store.get_evolution_transaction(transaction["transaction_id"])
    assert absorbed["status"] == "absorbed"

    rollback = await service.rollback(
        transaction["transaction_id"],
        reason="Rollback test confirms the prior source can be restored through Git history.",
    )
    assert rollback["status"] == "rollback_restart_pending"
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    await service.reconcile_startup(boot_count=3)
    rolled_back = store.get_evolution_transaction(transaction["transaction_id"])
    assert rolled_back["status"] == "rolled_back"


def test_external_recovery_restores_only_recent_exact_pending_commit(
    settings,
    tmp_path: Path,
) -> None:
    git_root = tmp_path / "repository"
    source = git_root / "value.txt"
    git_root.mkdir()
    source.write_text("stable\n", encoding="utf-8")
    _git(git_root, "init")
    _git(git_root, "add", ".")
    _git(
        git_root,
        "-c",
        "user.name=ECK Test",
        "-c",
        "user.email=eck-test@local",
        "commit",
        "-m",
        "stable",
    )
    previous = _git(git_root, "rev-parse", "HEAD").strip()
    source.write_text("broken\n", encoding="utf-8")
    _git(git_root, "add", ".")
    _git(
        git_root,
        "-c",
        "user.name=ECK Test",
        "-c",
        "user.email=eck-test@local",
        "commit",
        "-m",
        "candidate",
    )
    expected = _git(git_root, "rev-parse", "HEAD").strip()

    database = tmp_path / "data" / "eck.db"
    store = SQLiteStore(database)
    store.initialize()
    transaction = store.upsert_evolution_transaction(
        {
            "candidate_id": new_id("core-candidate"),
            "status": "approved",
            "base_commit": previous,
            "base_tree_sha256": "a" * 64,
            "candidate_tree_sha": "b" * 40,
            "patch_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
            "protected_paths": [],
            "fixed_gates": {"passed": True},
        }
    )
    store.update_evolution_transaction(
        transaction["transaction_id"],
        status="restart_pending",
        expected_commit_sha=expected,
        previous_commit_sha=previous,
        restart_nonce=new_id("restart"),
        activation_requested_at=iso_now(),
    )

    module = _recovery_module()
    result = module.recover_failed_activation(git_root, database)

    assert result["recovered"] is True
    assert _git(git_root, "rev-parse", "HEAD").strip() == previous
    assert source.read_text(encoding="utf-8") == "stable\n"
    recovered = store.get_evolution_transaction(transaction["transaction_id"])
    assert recovered["status"] == "rollback_restart_pending"
    assert recovered["expected_commit_sha"] == previous
    receipts = store.list_evolution_boot_receipts(transaction["transaction_id"])
    assert receipts[0]["status"] == "startup_failed_external"


def test_external_recovery_closes_commit_before_database_crash_window(
    settings,
    tmp_path: Path,
) -> None:
    git_root = tmp_path / "repository"
    source = git_root / "value.txt"
    git_root.mkdir()
    source.write_text("stable\n", encoding="utf-8")
    _git(git_root, "init")
    _git(git_root, "add", ".")
    _git(
        git_root,
        "-c",
        "user.name=ECK Test",
        "-c",
        "user.email=eck-test@local",
        "commit",
        "-m",
        "stable",
    )
    previous = _git(git_root, "rev-parse", "HEAD").strip()
    source.write_text("candidate\n", encoding="utf-8")
    _git(git_root, "add", ".")
    _git(
        git_root,
        "-c",
        "user.name=ECK Test",
        "-c",
        "user.email=eck-test@local",
        "commit",
        "-m",
        "candidate",
    )
    candidate_tree = _git(git_root, "rev-parse", "HEAD^{tree}").strip()

    database = tmp_path / "data" / "eck.db"
    store = SQLiteStore(database)
    store.initialize()
    transaction = store.upsert_evolution_transaction(
        {
            "candidate_id": new_id("core-candidate"),
            "status": "activation_applying",
            "base_commit": previous,
            "base_tree_sha256": "a" * 64,
            "candidate_tree_sha": candidate_tree,
            "patch_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
            "protected_paths": [],
            "fixed_gates": {"passed": True},
        }
    )
    store.update_evolution_transaction(
        transaction["transaction_id"],
        previous_commit_sha=previous,
        activation_requested_at=iso_now(),
    )

    result = _recovery_module().recover_failed_activation(git_root, database)

    assert result["recovered"] is True
    assert _git(git_root, "rev-parse", "HEAD").strip() == previous
    assert source.read_text(encoding="utf-8") == "stable\n"
    recovered = store.get_evolution_transaction(transaction["transaction_id"])
    assert recovered["status"] == "rollback_restart_pending"


def test_external_recovery_reopens_unapplied_activation(
    settings,
    tmp_path: Path,
) -> None:
    git_root = tmp_path / "repository"
    git_root.mkdir()
    (git_root / "value.txt").write_text("stable\n", encoding="utf-8")
    _git(git_root, "init")
    _git(git_root, "add", ".")
    _git(
        git_root,
        "-c",
        "user.name=ECK Test",
        "-c",
        "user.email=eck-test@local",
        "commit",
        "-m",
        "stable",
    )
    previous = _git(git_root, "rev-parse", "HEAD").strip()
    database = tmp_path / "data" / "eck.db"
    store = SQLiteStore(database)
    store.initialize()
    transaction = store.upsert_evolution_transaction(
        {
            "candidate_id": new_id("core-candidate"),
            "status": "activation_applying",
            "base_commit": previous,
            "base_tree_sha256": "a" * 64,
            "candidate_tree_sha": "b" * 40,
            "patch_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
            "protected_paths": [],
            "fixed_gates": {"passed": True},
        }
    )
    store.update_evolution_transaction(
        transaction["transaction_id"],
        previous_commit_sha=previous,
        activation_requested_at=iso_now(),
    )

    result = _recovery_module().recover_failed_activation(git_root, database)

    assert result == {"recovered": False, "reason": "activation_not_applied"}
    reopened = store.get_evolution_transaction(transaction["transaction_id"])
    assert reopened["status"] == "approved"
