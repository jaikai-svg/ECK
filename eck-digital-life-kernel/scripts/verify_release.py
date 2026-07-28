from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def run(name: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "output_tail": (completed.stdout + completed.stderr)[-4000:],
    }


def http_json(url: str, *, method: str = "GET") -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def live_acceptance() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="eck-release-") as temp:
        temp_path = Path(temp)
        port = 18420
        env = os.environ.copy()
        env.update(
            {
                "ECK_ENVIRONMENT": "test",
                "ECK_BRAIN_PROVIDER": "mock",
                "ECK_DATA_DIR": str(temp_path / "data"),
                "ECK_WORKSPACE_DIR": str(temp_path / "workspace"),
                "ECK_DATABASE_PATH": str(temp_path / "data" / "eck.db"),
                "ECK_HEARTBEAT_SECONDS": "1",
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "eck.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            health: dict[str, Any] | None = None
            for _ in range(50):
                if process.poll() is not None:
                    break
                try:
                    health = http_json(f"http://127.0.0.1:{port}/health")
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            if health is None:
                output = process.stdout.read() if process.stdout else ""
                return {
                    "status": "failed",
                    "reason": "server did not become healthy",
                    "server_output": output[-4000:],
                }
            demos = http_json(
                f"http://127.0.0.1:{port}/v1/demos/all",
                method="POST",
            )
            checks = {
                "health": health["status"] == "ok",
                "event_chain": health["event_chain"]["valid"],
                "persistence": demos["persistence"]["acceptance"],
                "safe_code": demos["safe_code"]["status"] == "verified_success",
                "gridworld_learning": demos["gridworld"]["learning_measure"][
                    "fewer_steps_after_experience"
                ],
            }
            return {
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "gridworld": demos["gridworld"]["learning_measure"],
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def tree_digest() -> str:
    digest = hashlib.sha256()
    excluded = {".venv", ".git", "artifacts", "__pycache__"}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in excluded for part in path.parts):
            continue
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    checks = [
        run("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        run("mypy", [sys.executable, "-m", "mypy", "src/eck"]),
        run("tests", [sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q"]),
        run("coverage", [sys.executable, "-m", "coverage", "report"]),
    ]
    acceptance = live_acceptance()
    optional = {
        "docker": (
            run("docker_build", ["docker", "build", "-t", "eck:release-check", "."])
            if shutil.which("docker")
            else {"status": "skipped", "reason": "docker executable unavailable"}
        ),
        "rust": (
            run("cargo_test", ["cargo", "test", "--workspace"])
            if shutil.which("cargo")
            else {"status": "skipped", "reason": "cargo executable unavailable"}
        ),
    }
    required_passed = all(item["status"] == "passed" for item in checks) and (
        acceptance["status"] == "passed"
    )
    report = {
        "schema_version": "eck-release-report.v1",
        "version": "0.1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "required_status": "passed" if required_passed else "failed",
        "checks": checks,
        "live_acceptance": acceptance,
        "optional_environment_checks": optional,
        "source_tree_sha256": tree_digest(),
        "truthfulness_note": (
            "Skipped checks are not treated as passed. Docker and Rust are independently "
            "verified only when their executables are available."
        ),
    }
    output = ARTIFACTS / "release-report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if required_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

