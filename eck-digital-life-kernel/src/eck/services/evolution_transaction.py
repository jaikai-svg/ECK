from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any

from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.events.bus import EventBus
from eck.services.evolution_policy import EvolutionProtectedSurfacePolicy
from eck.storage.sqlite import SQLiteStore


class EvolutionTransactionService:
    """Reviewed core evolution with exact fingerprints and restart receipts."""

    _eligible_evaluation_verdicts = {
        "verified_improvement",
        "verified_performance_improvement",
        "verified_maintenance_non_regression",
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        project_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        self.metadata_root = settings.evolution_dir / "core_candidates"
        self.heldout_root = settings.evolution_dir / "heldout_packs"
        self.heldout_root.mkdir(parents=True, exist_ok=True)
        self.policy = EvolutionProtectedSurfacePolicy(self.project_root)

    def status(self) -> dict[str, Any]:
        transactions = self.store.list_evolution_transactions(limit=500)
        counts: dict[str, int] = {}
        for item in transactions:
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        return {
            "schema_version": "eck-evolution-transaction.v1",
            "transaction_count": len(transactions),
            "status_counts": counts,
            "latest": self._details(transactions[0]) if transactions else None,
            "heldout_pack_count": len(self.list_heldout_packs()),
            "activation": "human_approved_commit_then_graceful_restart",
            "hot_live_mutation": False,
            "catastrophic_startup_rollback": "verified_windows_desktop_launcher",
            "protected_surfaces": self.policy.status(),
        }

    def list_transactions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._details(item)
            for item in self.store.list_evolution_transactions(limit=limit)
        ]

    def get(self, transaction_id: str) -> dict[str, Any]:
        return self._details(self.store.get_evolution_transaction(transaction_id))

    def get_for_candidate(self, candidate_id: str) -> dict[str, Any]:
        return self._details(
            self.store.get_evolution_transaction_for_candidate(candidate_id)
        )

    def observe_candidate(self, manifest: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(manifest["candidate_id"])
        try:
            existing = self.store.get_evolution_transaction_for_candidate(candidate_id)
        except KeyError:
            existing = None
        if existing is not None and existing["status"] not in {
            "drafted",
            "fixed_gates_failed",
            "awaiting_heldout_evaluation",
        }:
            raise RuntimeError(
                "A sealed evolution transaction cannot be replaced by candidate observation."
            )
        candidate_status = str(manifest.get("status", "drafted"))
        status = {
            "drafted": "drafted",
            "rejected_by_fixed_gates": "fixed_gates_failed",
            "validated_awaiting_human": "awaiting_heldout_evaluation",
        }.get(candidate_status, candidate_status)
        record = self.store.upsert_evolution_transaction(
            {
                "candidate_id": candidate_id,
                "status": status,
                "base_commit": manifest.get("source_commit", ""),
                "base_tree_sha256": manifest.get("source_tree_sha256", ""),
                "candidate_tree_sha": manifest.get("candidate_tree_sha", ""),
                "patch_sha256": manifest.get("patch_sha256", ""),
                "manifest_sha256": self._manifest_sha256(manifest),
                "protected_paths": manifest.get("protected_paths", []),
                "fixed_gates": manifest.get("validation", {}),
                "created_at": manifest.get("created_at"),
            }
        )
        return self._details(record)

    async def register_heldout_pack(
        self,
        *,
        pack_id: str,
        description: str,
        test_files: tuple[str, ...],
        change_kind: str,
        minimum_speedup_percent: float,
        allow_non_regression: bool,
    ) -> dict[str, Any]:
        pack_dir = self._pack_dir(pack_id)
        rows = self._heldout_test_rows(pack_dir, test_files)
        manifest = {
            "schema_version": "eck-heldout-pack.v1",
            "pack_id": pack_id,
            "description": description,
            "change_kind": change_kind,
            "minimum_speedup_percent": float(minimum_speedup_percent),
            "allow_non_regression": bool(allow_non_regression),
            "tests": rows,
            "created_at": iso_now(),
        }
        manifest["pack_sha256"] = self._canonical_sha256(manifest)
        self._atomic_json(pack_dir / "manifest.json", manifest)
        await self.events.publish(
            "EvolutionHeldoutPackRegistered",
            pack_id,
            {
                "pack_sha256": manifest["pack_sha256"],
                "test_count": len(rows),
                "change_kind": change_kind,
            },
        )
        return manifest

    def list_heldout_packs(self) -> list[dict[str, Any]]:
        packs: list[dict[str, Any]] = []
        for path in self.heldout_root.glob("*/manifest.json"):
            with suppress(OSError, ValueError, json.JSONDecodeError):
                packs.append(self._load_pack(path.parent.name))
        return sorted(packs, key=lambda item: str(item.get("created_at", "")), reverse=True)

    async def evaluate(self, candidate_id: str, pack_id: str) -> dict[str, Any]:
        transaction = self.store.get_evolution_transaction_for_candidate(candidate_id)
        if transaction["status"] not in {
            "awaiting_heldout_evaluation",
            "heldout_failed",
            "no_measurable_improvement",
        }:
            raise RuntimeError(
                f"Candidate is not eligible for held-out evaluation: {transaction['status']}"
            )
        manifest = self._candidate_manifest(candidate_id)
        self._verify_transaction_manifest(transaction, manifest)
        candidate_project = Path(str(manifest["project_path"])).resolve()
        if not candidate_project.is_dir():
            raise FileNotFoundError("The isolated candidate checkout is missing.")
        pack = self._load_pack(pack_id)
        test_paths = self._verify_pack_files(pack)
        transaction_id = str(transaction["transaction_id"])
        with self._baseline_checkout(transaction_id, str(transaction["base_commit"])) as baseline:
            baseline_project = baseline / self.project_root.relative_to(self._git_root())
            baseline_result, candidate_result = await asyncio.gather(
                self._run_heldout_suite(baseline_project, test_paths),
                self._run_heldout_suite(candidate_project, test_paths),
            )
        assessment = self._assess_evaluation(baseline_result, candidate_result, pack)
        evaluation = self.store.create_evolution_evaluation(
            {
                "transaction_id": transaction_id,
                "pack_id": pack_id,
                "pack_sha256": pack["pack_sha256"],
                "baseline": baseline_result,
                "candidate": candidate_result,
                "result": assessment,
                "verdict": assessment["verdict"],
                "improvement_score": assessment["improvement_score"],
            }
        )
        next_status = (
            "awaiting_human_approval"
            if assessment["verdict"] in self._eligible_evaluation_verdicts
            else (
                "heldout_failed"
                if assessment["verdict"] == "regression"
                else "no_measurable_improvement"
            )
        )
        updated = self.store.update_evolution_transaction(
            transaction_id,
            status=next_status,
            error="" if next_status == "awaiting_human_approval" else assessment["summary"],
        )
        await self.events.publish(
            "EvolutionHeldoutEvaluated",
            transaction_id,
            {
                "candidate_id": candidate_id,
                "evaluation_id": evaluation["evaluation_id"],
                "verdict": assessment["verdict"],
                "improvement_score": assessment["improvement_score"],
            },
            correlation_id=candidate_id,
        )
        return self._details(updated)

    async def approve(
        self,
        candidate_id: str,
        *,
        approved_by: str,
        reason: str,
        confirmed_candidate_tree_sha: str,
    ) -> dict[str, Any]:
        transaction = self.store.get_evolution_transaction_for_candidate(candidate_id)
        if transaction["status"] != "awaiting_human_approval":
            raise RuntimeError(
                f"Candidate is not awaiting human approval: {transaction['status']}"
            )
        if confirmed_candidate_tree_sha != transaction["candidate_tree_sha"]:
            raise ValueError("Confirmed candidate tree does not match the validated tree.")
        evaluations = self.store.list_evolution_evaluations(
            str(transaction["transaction_id"])
        )
        if not evaluations or evaluations[0]["verdict"] not in self._eligible_evaluation_verdicts:
            raise RuntimeError("No eligible held-out evaluation is bound to this candidate.")
        now = iso_now()
        updated = self.store.update_evolution_transaction(
            str(transaction["transaction_id"]),
            status="approved",
            approval={
                "approved_by": approved_by,
                "reason": reason,
                "confirmed_candidate_tree_sha": confirmed_candidate_tree_sha,
                "evaluation_id": evaluations[0]["evaluation_id"],
                "approved_at": now,
            },
            approved_at=now,
            error="",
        )
        await self.events.publish(
            "EvolutionCandidateApproved",
            str(transaction["transaction_id"]),
            {"candidate_id": candidate_id, "approved_by": approved_by},
            correlation_id=candidate_id,
        )
        return self._details(updated)

    async def activate(
        self,
        candidate_id: str,
        *,
        confirmed_candidate_tree_sha: str,
        reason: str,
    ) -> dict[str, Any]:
        transaction = self.store.get_evolution_transaction_for_candidate(candidate_id)
        if transaction["status"] != "approved":
            raise RuntimeError(f"Candidate is not approved: {transaction['status']}")
        if confirmed_candidate_tree_sha != transaction["candidate_tree_sha"]:
            raise ValueError("Activation tree confirmation does not match the approved tree.")
        manifest = self._candidate_manifest(candidate_id)
        self._verify_transaction_manifest(transaction, manifest)
        self._verify_candidate_git_state(transaction, manifest)
        git_root = self._git_root()
        if self._git(git_root, "status", "--porcelain").strip():
            raise RuntimeError("Activation requires a clean live source tree.")
        base_commit = str(transaction["base_commit"])
        current_commit = self._git(git_root, "rev-parse", "HEAD").strip()
        if current_commit != base_commit:
            raise RuntimeError("Live HEAD moved after candidate creation; rebase and revalidate.")
        patch_path = self._candidate_dir(candidate_id) / "candidate.patch"
        transaction_id = str(transaction["transaction_id"])
        self.store.update_evolution_transaction(
            transaction_id,
            status="activation_applying",
            previous_commit_sha=base_commit,
            activation_requested_at=iso_now(),
            error="",
        )
        try:
            self._git(git_root, "apply", "--check", "--index", str(patch_path))
            self._git(git_root, "apply", "--index", str(patch_path))
            observed_tree = self._git(git_root, "write-tree").strip()
            if observed_tree != transaction["candidate_tree_sha"]:
                raise RuntimeError("Applied live tree does not match the approved candidate tree.")
            subject = re.sub(r"\s+", " ", str(manifest.get("objective", "core update"))).strip()
            self._git(
                git_root,
                "-c",
                "user.name=ECK",
                "-c",
                "user.email=eck@local",
                "commit",
                "-m",
                f"ECK evolution: {subject[:68]}",
            )
            expected_commit = self._git(git_root, "rev-parse", "HEAD").strip()
        except Exception:
            with suppress(RuntimeError):
                self._git(git_root, "reset", "--hard", base_commit)
            self.store.update_evolution_transaction(
                transaction_id,
                status="approved",
                error="Activation failed before restart preparation.",
            )
            raise
        nonce = new_id("restart")
        updated = self.store.update_evolution_transaction(
            transaction_id,
            status="restart_pending",
            expected_commit_sha=expected_commit,
            previous_commit_sha=base_commit,
            restart_nonce=nonce,
            activation_requested_at=iso_now(),
            error="",
        )
        await self.events.publish(
            "EvolutionActivationPrepared",
            transaction_id,
            {
                "candidate_id": candidate_id,
                "expected_commit_sha": expected_commit,
                "previous_commit_sha": base_commit,
                "reason": reason,
                "restart_nonce": nonce,
            },
            correlation_id=candidate_id,
        )
        result = self._details(updated)
        result["restart_required"] = True
        return result

    async def rollback(self, transaction_id: str, *, reason: str) -> dict[str, Any]:
        transaction = self.store.get_evolution_transaction(transaction_id)
        if transaction["status"] not in {
            "restart_pending",
            "absorbed",
            "startup_mismatch",
        }:
            raise RuntimeError(f"Transaction cannot be rolled back: {transaction['status']}")
        git_root = self._git_root()
        if self._git(git_root, "status", "--porcelain").strip():
            raise RuntimeError("Rollback requires a clean live source tree.")
        expected = str(transaction["expected_commit_sha"] or "")
        observed = self._git(git_root, "rev-parse", "HEAD").strip()
        if not expected or observed != expected:
            raise RuntimeError("Live HEAD no longer matches the activated evolution commit.")
        self._git(
            git_root,
            "-c",
            "user.name=ECK",
            "-c",
            "user.email=eck@local",
            "revert",
            "--no-edit",
            expected,
        )
        rollback_commit = self._git(git_root, "rev-parse", "HEAD").strip()
        updated = self.store.update_evolution_transaction(
            transaction_id,
            status="rollback_restart_pending",
            expected_commit_sha=rollback_commit,
            rollback_commit_sha=rollback_commit,
            restart_nonce=new_id("restart"),
            activation_requested_at=iso_now(),
            error=reason,
        )
        await self.events.publish(
            "EvolutionRollbackPrepared",
            transaction_id,
            {"rollback_commit_sha": rollback_commit, "reason": reason},
        )
        result = self._details(updated)
        result["restart_required"] = True
        return result

    async def reconcile_startup(self, *, boot_count: int) -> list[dict[str, Any]]:
        pending = [
            item
            for item in self.store.list_evolution_transactions(limit=50)
            if item["status"]
            in {"activation_applying", "restart_pending", "rollback_restart_pending"}
        ]
        if not pending:
            return []
        observed = self._git(self._git_root(), "rev-parse", "HEAD").strip()
        receipts: list[dict[str, Any]] = []
        for transaction in pending:
            expected = str(transaction["expected_commit_sha"] or "")
            applying = transaction["status"] == "activation_applying"
            if (
                applying
                and observed == transaction["base_commit"]
                and not self._git(
                    self._git_root(), "status", "--porcelain"
                ).strip()
            ):
                self.store.update_evolution_transaction(
                    str(transaction["transaction_id"]),
                    status="approved",
                    error="Activation stopped before the candidate commit was created.",
                )
                continue
            matched = bool(expected and expected == observed)
            if applying:
                try:
                    parent = self._git(self._git_root(), "rev-parse", f"{observed}^").strip()
                    tree = self._git(
                        self._git_root(), "rev-parse", f"{observed}^{{tree}}"
                    ).strip()
                    matched = bool(
                        parent == transaction["base_commit"]
                        and tree == transaction["candidate_tree_sha"]
                        and not self._git(
                            self._git_root(), "status", "--porcelain"
                        ).strip()
                    )
                except RuntimeError:
                    matched = False
                if matched:
                    expected = observed
            rolling_back = transaction["status"] == "rollback_restart_pending"
            receipt_status = "verified" if matched else "mismatch"
            receipt = self.store.create_evolution_boot_receipt(
                {
                    "transaction_id": transaction["transaction_id"],
                    "expected_commit_sha": expected,
                    "observed_commit_sha": observed,
                    "boot_count": boot_count,
                    "status": receipt_status,
                    "details": {
                        "restart_nonce": transaction["restart_nonce"],
                        "mode": "rollback" if rolling_back else "activation",
                    },
                }
            )
            now = iso_now()
            if matched:
                next_status = "rolled_back" if rolling_back else "absorbed"
                self.store.update_evolution_transaction(
                    str(transaction["transaction_id"]),
                    status=next_status,
                    expected_commit_sha=expected,
                    restart_verified_at=now,
                    completed_at=now,
                    error="",
                )
            else:
                next_status = "startup_mismatch"
                self.store.update_evolution_transaction(
                    str(transaction["transaction_id"]),
                    status=next_status,
                    error=(
                        f"Expected startup commit {expected}, observed {observed}. "
                        "Automatic destructive rollback was not attempted."
                    ),
                )
            await self.events.publish(
                "EvolutionStartupVerified" if matched else "EvolutionStartupMismatch",
                str(transaction["transaction_id"]),
                {
                    "expected_commit_sha": expected,
                    "observed_commit_sha": observed,
                    "boot_count": boot_count,
                    "result": next_status,
                },
            )
            receipts.append(receipt)
        return receipts

    def _details(self, transaction: dict[str, Any]) -> dict[str, Any]:
        value = dict(transaction)
        transaction_id = str(value["transaction_id"])
        value["evaluations"] = self.store.list_evolution_evaluations(transaction_id)
        value["boot_receipts"] = self.store.list_evolution_boot_receipts(transaction_id)
        return value

    def _candidate_manifest(self, candidate_id: str) -> dict[str, Any]:
        path = self._candidate_dir(candidate_id) / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Core candidate manifest must be a JSON object.")
        return value

    def _candidate_dir(self, candidate_id: str) -> Path:
        if not re.fullmatch(r"core-candidate_[a-f0-9]{32}", candidate_id):
            raise ValueError("Invalid core candidate ID.")
        path = (self.metadata_root / candidate_id).resolve()
        path.relative_to(self.metadata_root.resolve())
        return path

    def _verify_transaction_manifest(
        self,
        transaction: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        if self._manifest_sha256(manifest) != transaction["manifest_sha256"]:
            raise RuntimeError("Candidate manifest changed after transaction sealing.")
        patch_path = self._candidate_dir(str(transaction["candidate_id"])) / "candidate.patch"
        patch = patch_path.read_bytes()
        if hashlib.sha256(patch).hexdigest() != transaction["patch_sha256"]:
            raise RuntimeError("Candidate patch changed after fixed validation.")

    def _verify_candidate_git_state(
        self,
        transaction: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        candidate_project = Path(str(manifest["project_path"])).resolve()
        patch = self._git(
            candidate_project,
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "--",
            ".",
        ).encode("utf-8")
        if hashlib.sha256(patch).hexdigest() != transaction["patch_sha256"]:
            raise RuntimeError("Candidate staged patch no longer matches the sealed patch.")
        tree = self._git(candidate_project, "write-tree").strip()
        if tree != transaction["candidate_tree_sha"]:
            raise RuntimeError("Candidate Git tree no longer matches the approved tree.")

    def _load_pack(self, pack_id: str) -> dict[str, Any]:
        path = self._pack_dir(pack_id) / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Held-out pack manifest must be a JSON object.")
        expected = str(value.get("pack_sha256", ""))
        unsigned = dict(value)
        unsigned.pop("pack_sha256", None)
        if expected != self._canonical_sha256(unsigned):
            raise ValueError("Held-out pack manifest hash is invalid.")
        return value

    def _verify_pack_files(self, pack: dict[str, Any]) -> tuple[Path, ...]:
        pack_dir = self._pack_dir(str(pack["pack_id"]))
        paths: list[Path] = []
        for item in pack.get("tests", []):
            relative = self._safe_test_path(str(item["path"]))
            path = (pack_dir / relative).resolve()
            path.relative_to(pack_dir)
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(f"Held-out test changed after registration: {relative}")
            paths.append(path)
        if not paths:
            raise ValueError("Held-out pack contains no tests.")
        return tuple(paths)

    async def _run_heldout_suite(
        self,
        project: Path,
        tests: tuple[Path, ...],
    ) -> dict[str, Any]:
        results = []
        started = time.perf_counter()
        for test_path in tests:
            result = await self._run_test(project, test_path)
            results.append(result)
        duration = time.perf_counter() - started
        passed = sum(1 for item in results if item["status"] == "passed")
        return {
            "project": str(project),
            "test_count": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / len(results) if results else 0.0,
            "duration_seconds": round(duration, 6),
            "tests": results,
        }

    async def _run_test(self, project: Path, test_path: Path) -> dict[str, Any]:
        env = os.environ.copy()
        source_path = str(project / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = source_path + (os.pathsep + existing if existing else "")
        started = time.perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(test_path),
                cwd=project,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.core_evolution_timeout_seconds,
            )
            output = stdout.decode("utf-8", errors="replace")[-8000:]
            returncode = process.returncode
        except TimeoutError:
            process.kill()
            await process.wait()
            output = "Held-out test timed out."
            returncode = None
        return {
            "test": test_path.name,
            "sha256": hashlib.sha256(test_path.read_bytes()).hexdigest(),
            "status": "passed" if returncode == 0 else "failed",
            "returncode": returncode,
            "duration_seconds": round(time.perf_counter() - started, 6),
            "output_tail": output,
        }

    @staticmethod
    def _assess_evaluation(
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        pack: dict[str, Any],
    ) -> dict[str, Any]:
        baseline_rate = float(baseline["pass_rate"])
        candidate_rate = float(candidate["pass_rate"])
        pass_delta = candidate_rate - baseline_rate
        baseline_duration = float(baseline["duration_seconds"])
        candidate_duration = float(candidate["duration_seconds"])
        speedup = (
            ((baseline_duration - candidate_duration) / baseline_duration) * 100
            if baseline_duration > 0 and baseline_rate == candidate_rate == 1.0
            else 0.0
        )
        minimum_speedup = float(pack.get("minimum_speedup_percent", 0.0))
        all_candidate_passed = candidate_rate == 1.0
        if candidate_rate < baseline_rate or not all_candidate_passed:
            verdict = "regression"
            summary = "Candidate failed held-out checks or regressed below baseline."
        elif pass_delta > 0:
            verdict = "verified_improvement"
            summary = "Candidate passed held-out checks that the baseline failed."
        elif minimum_speedup > 0 and speedup >= minimum_speedup:
            verdict = "verified_performance_improvement"
            summary = "Candidate met the pre-registered held-out performance threshold."
        elif bool(pack.get("allow_non_regression")):
            verdict = "verified_maintenance_non_regression"
            summary = "Candidate preserved all held-out behavior for a maintenance change."
        else:
            verdict = "no_measurable_improvement"
            summary = "Candidate passed, but no pre-registered objective improvement was measured."
        return {
            "verdict": verdict,
            "summary": summary,
            "pass_rate_delta": round(pass_delta, 6),
            "speedup_percent": round(speedup, 3),
            "minimum_speedup_percent": minimum_speedup,
            "improvement_score": round(pass_delta + max(0.0, speedup) / 1000, 6),
            "eligible_for_human_approval": verdict
            in EvolutionTransactionService._eligible_evaluation_verdicts,
        }

    @contextmanager
    def _baseline_checkout(self, transaction_id: str, commit: str) -> Iterator[Path]:
        git_root = self._git_root()
        checkout = git_root.parent / ".eck-evolution-baselines" / transaction_id
        checkout.parent.mkdir(parents=True, exist_ok=True)
        self._git(git_root, "worktree", "add", "--detach", str(checkout), commit)
        try:
            yield checkout
        finally:
            with suppress(RuntimeError):
                self._git(git_root, "worktree", "remove", "--force", str(checkout))

    def _heldout_test_rows(
        self,
        pack_dir: Path,
        test_files: tuple[str, ...],
    ) -> list[dict[str, str]]:
        if not test_files:
            raise ValueError("At least one held-out test file is required.")
        rows: list[dict[str, str]] = []
        for value in test_files:
            relative = self._safe_test_path(value)
            path = (pack_dir / relative).resolve()
            path.relative_to(pack_dir)
            if not path.is_file():
                raise FileNotFoundError(f"Held-out test file is missing: {relative}")
            rows.append(
                {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        return rows

    def _pack_dir(self, pack_id: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,79}", pack_id):
            raise ValueError("Invalid held-out pack ID.")
        path = (self.heldout_root / pack_id).resolve()
        path.relative_to(self.heldout_root.resolve())
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _safe_test_path(value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
            raise ValueError(f"Unsafe held-out test path: {value}")
        return path.as_posix()

    def _git_root(self) -> Path:
        for candidate in (self.project_root, *self.project_root.parents):
            if (candidate / ".git").exists():
                return candidate
        raise RuntimeError("Evolution transactions require a Git repository.")

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
            raise RuntimeError(detail)
        return result.stdout

    @staticmethod
    def _manifest_sha256(manifest: dict[str, Any]) -> str:
        return EvolutionTransactionService._canonical_sha256(manifest)

    @staticmethod
    def _canonical_sha256(value: dict[str, Any]) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
