from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check ECK architecture stability gates.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def load_baseline(root: Path) -> dict[str, Any]:
    path = root / "config" / "architecture-baseline.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Architecture baseline must be a JSON object.")
    return value


def imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def check(root: Path) -> list[str]:
    baseline = load_baseline(root)
    errors: list[str] = []
    tracked = baseline["line_ratcheted_files"]
    default_python = int(baseline["default_python_max_lines"])
    default_frontend = int(baseline["default_frontend_max_lines"])
    allow_experimental = set(baseline["experimental_import_allowlist"])

    source_root = root / "src" / "eck"
    for path in source_root.rglob("*"):
        if "node_modules" in path.parts:
            continue
        if not path.is_file() or path.suffix not in {".py", ".js", ".css", ".html"}:
            continue
        relative = path.relative_to(root).as_posix()
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        limit = tracked.get(
            relative,
            default_python if path.suffix == ".py" else default_frontend,
        )
        if line_count > int(limit):
            errors.append(f"Line budget exceeded: {relative} has {line_count}, limit {limit}.")

    for path in source_root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        modules = imported_modules(path)
        if (
            not relative.startswith("src/eck/experimental/")
            and relative not in allow_experimental
            and any(name.startswith("eck.experimental") for name in modules)
        ):
            errors.append(f"Stable module imports experimental code directly: {relative}.")
        if relative.startswith("src/eck/modules/") and any(
            name.startswith(("eck.api", "eck.dashboard", "eck.experimental"))
            for name in modules
        ):
            errors.append(f"Stable bounded module crosses an outer-layer boundary: {relative}.")
        if relative.startswith(("src/eck/domain/", "src/eck/core/")) and any(
            name.startswith(("eck.api", "eck.services", "eck.experimental"))
            for name in modules
        ):
            errors.append(f"Core/domain module depends on an orchestration layer: {relative}.")
    return errors


def main() -> int:
    args = parse_args()
    errors = check(args.root.resolve())
    if errors:
        print("\n".join(errors))
        return 1
    print("ECK architecture gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
