from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False))


def install_dependencies(manifest: dict[str, Any]) -> None:
    python_packages = []
    npm_packages = []
    pattern = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~].+)?$")
    for dependency in manifest.get("dependencies", []):
        value = str(dependency).strip()
        if value.startswith("npm:"):
            package = value[4:]
            if not pattern.fullmatch(package):
                raise ValueError(f"Unsupported npm dependency specification: {package}")
            npm_packages.append(package)
        else:
            if not pattern.fullmatch(value):
                raise ValueError(f"Unsupported PyPI dependency specification: {value}")
            python_packages.append(value)
    if python_packages:
        python_dir = Path("/tmp/python")
        python_dir.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-input",
                "--target",
                str(python_dir),
                *python_packages,
            ],
            check=True,
            timeout=180,
        )
        sys.path.insert(0, str(python_dir))
    if npm_packages:
        npm_dir = Path("/tmp/npm")
        npm_dir.mkdir()
        subprocess.run(
            ["npm", "install", "--ignore-scripts", "--prefix", str(npm_dir), *npm_packages],
            check=True,
            timeout=180,
        )
        os.environ["NODE_PATH"] = str(npm_dir / "node_modules")


def load_skill(entrypoint: str):
    path = Path("/skill") / entrypoint
    if not path.is_file():
        path = Path("/opt/eck-worker/foundation_skill.py")
    spec = importlib.util.spec_from_file_location("eck_runtime_skill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load skill entrypoint: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in {"", "self-check"}:
        emit(
            {
                "success": True,
                "mode": "self-check",
                "detail": (
                    "ECK skill worker image is ready. Skills are launched by the ECK "
                    "core with read-only /skill and /request mounts."
                ),
            }
        )
        return 0
    if mode not in {"validate", "execute"}:
        emit({"success": False, "detail": f"Unknown worker mode: {mode}"})
        return 2
    manifest_path = Path("/request/manifest.json")
    if not manifest_path.is_file():
        emit(
            {
                "success": False,
                "error": "WorkerProtocolError",
                "detail": (
                    "Missing /request/manifest.json. Start skills through the ECK core "
                    "instead of launching validate/execute directly."
                ),
            }
        )
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    install_dependencies(manifest)
    if mode == "validate":
        test_file = Path("/skill/test_skill.py")
        if not test_file.is_file():
            emit({"success": False, "detail": "test_skill.py is required."})
            return 1
        module = load_skill(str(manifest.get("entrypoint", "skill.py")))
        if not callable(getattr(module, "execute", None)):
            emit(
                {
                    "success": False,
                    "detail": (
                        "Skill entrypoint must define execute(operation, payload, context)."
                    ),
                }
            )
            return 1
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                str(test_file),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        emit(
            {
                "success": result.returncode == 0,
                "test_output": (result.stdout + result.stderr)[-8000:],
            }
        )
        return result.returncode
    if mode == "execute":
        request = json.loads(Path("/request/request.json").read_text(encoding="utf-8"))
        module = load_skill(str(manifest.get("entrypoint", "skill.py")))
        execute = getattr(module, "execute", None)
        if not callable(execute):
            raise RuntimeError("Skill entrypoint must define execute(operation, payload, context).")
        result = execute(
            request.get("operation", ""),
            request.get("payload", {}),
            {
                "skill_name": manifest["name"],
                "output_dir": "/output",
                "permissions": manifest.get("permissions", []),
            },
        )
        emit({"success": True, "result": result})
        return 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit({"success": False, "error": type(exc).__name__, "detail": str(exc)})
        raise SystemExit(1) from exc
