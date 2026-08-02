from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eck import __version__

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def run(
    name: str,
    command: list[str],
    *,
    timeout_seconds: float = 300,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else exc.stdout
        )
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else exc.stderr
        )
        output = f"Timed out after {timeout_seconds:g}s.\n{stdout or ''}{stderr or ''}"
        return {
            "name": name,
            "status": "failed",
            "returncode": None,
            "output_tail": output[-4000:],
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "failed",
            "returncode": None,
            "output_tail": f"{type(exc).__name__}: {exc}",
        }
    return {
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "output_tail": ((completed.stdout or "") + (completed.stderr or ""))[-4000:],
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
    import uvicorn

    from eck.api.main import create_api
    from eck.app import build_application
    from eck.config import Settings

    with tempfile.TemporaryDirectory(prefix="eck-release-") as temp:
        temp_path = Path(temp)
        port = 18420
        workspace = temp_path / "workspace"
        settings = Settings(
            environment="test",
            brain_provider="mock",
            data_dir=temp_path / "data",
            workspace_dir=workspace,
            database_path=temp_path / "data" / "eck.db",
            image_model_dir=workspace / "models",
            image_output_dir=workspace / "generated_images",
            forge_root=workspace / "forge",
            rembg_model_dir=workspace / "rembg" / "models",
            image_generation_enabled=False,
            skill_worker_enabled=False,
            network_enabled=False,
            supervisor_enabled=False,
            auto_start_kernel=True,
            heartbeat_seconds=1,
        )
        application = build_application(settings)
        server = uvicorn.Server(
            uvicorn.Config(
                create_api(application=application),
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )
        thread = threading.Thread(target=server.run, name="eck-release-uvicorn", daemon=True)
        thread.start()
        try:
            health: dict[str, Any] | None = None
            for _ in range(50):
                if not thread.is_alive():
                    break
                try:
                    health = http_json(f"http://127.0.0.1:{port}/health")
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            if health is None:
                return {
                    "status": "failed",
                    "reason": "server did not become healthy",
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
            server.should_exit = True
            thread.join(timeout=15)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=5)


def tree_digest() -> str:
    digest = hashlib.sha256()
    excluded = {
        ".coverage",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "artifacts",
        "htmlcov",
        "workspace",
    }
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
            run(
                "docker_build",
                ["docker", "build", "-t", "eck:release-check", "."],
                timeout_seconds=300,
            )
            if shutil.which("docker")
            else {"status": "skipped", "reason": "docker executable unavailable"}
        ),
        "rust": (
            run(
                "cargo_test",
                ["cargo", "test", "--workspace"],
                timeout_seconds=300,
            )
            if shutil.which("cargo")
            else {"status": "skipped", "reason": "cargo executable unavailable"}
        ),
    }
    optional_available_passed = all(
        item["status"] in {"passed", "skipped"} for item in optional.values()
    )
    required_passed = (
        all(item["status"] == "passed" for item in checks)
        and acceptance["status"] == "passed"
        and optional_available_passed
    )
    report = {
        "schema_version": "eck-release-report.v1",
        "version": __version__,
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
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if required_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
