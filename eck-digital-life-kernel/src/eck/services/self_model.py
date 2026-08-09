from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from eck.config import Settings
from eck.core.time import utc_now


class RepositorySelfModelService:
    _excluded_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "artifacts",
        "data",
        "dist",
        "htmlcov",
        "workspace",
    }
    _text_suffixes = {
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".rs",
        ".toml",
        ".yaml",
        ".yml",
    }

    def __init__(self, settings: Settings, project_root: Path | None = None) -> None:
        self.settings = settings
        self.project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        self.model_path = settings.self_model_path

    def ensure(self) -> dict[str, Any]:
        if not self.model_path.is_file():
            return self.refresh()
        recorded = self._read()
        current_git = self._git_state()
        recorded_git = recorded.get("git", {})
        if not isinstance(recorded_git, dict) or any(
            recorded_git.get(key) != current_git.get(key)
            for key in ("available", "commit", "dirty", "changed_paths")
        ):
            return self.refresh()
        generated_at = str(recorded.get("generated_at", ""))
        try:
            stale = utc_now() - self._parse_time(generated_at) > timedelta(hours=24)
        except ValueError:
            stale = True
        if stale:
            return self.refresh()
        return self.status()

    def status(self) -> dict[str, Any]:
        if not self.model_path.is_file():
            return {
                "schema_version": "eck-repository-self-model.v2",
                "initialized": False,
                "stale": True,
                "path": str(self.model_path),
            }
        model = self._read()
        generated_at = str(model.get("generated_at", ""))
        try:
            stale = utc_now() - self._parse_time(generated_at) > timedelta(hours=24)
        except ValueError:
            stale = True
        return {
            **model,
            "initialized": True,
            "stale": stale,
            "path": str(self.model_path),
        }

    def refresh(self) -> dict[str, Any]:
        files = self._source_files()
        modules: list[dict[str, Any]] = []
        inventory: list[dict[str, Any]] = []
        digest = hashlib.sha256()
        total_bytes = 0
        for path in files:
            relative = path.relative_to(self.project_root).as_posix()
            data = path.read_bytes()
            file_hash = hashlib.sha256(data).hexdigest()
            digest.update(relative.encode("utf-8"))
            digest.update(file_hash.encode("ascii"))
            total_bytes += len(data)
            inventory.append(
                {
                    "path": relative,
                    "bytes": len(data),
                    "sha256": file_hash,
                    "kind": self._kind(relative),
                }
            )
            if path.suffix == ".py":
                modules.append(self._python_module(path, relative))
        architecture = self._architecture(modules)
        model = {
            "schema_version": "eck-repository-self-model.v2",
            "generated_at": utc_now().isoformat(),
            "project_root": str(self.project_root),
            "source_tree_sha256": digest.hexdigest(),
            "git": self._git_state(),
            "summary": {
                "files": len(inventory),
                "python_modules": len(modules),
                "definitions": sum(len(item["definitions"]) for item in modules),
                "imports": sum(len(item["imports"]) for item in modules),
                "calls": sum(len(item["calls"]) for item in modules),
                "api_routes": sum(len(item["api_routes"]) for item in modules),
                "tests": sum(item["kind"] == "test" for item in inventory),
                "source_bytes": total_bytes,
            },
            "architecture": architecture,
            "python_modules": modules,
            "files": inventory,
            "boundaries": {
                "live_core": "src/eck",
                "verification": ["tests", "scripts/verify_release.py"],
                "runtime_mutation": "workspace/runtime_skills",
                "structural_candidates": "workspace/evolution/core_candidates",
                "identity": "data/identity",
                "large_artifacts_excluded": ["workspace", "data", "artifacts"],
            },
            "claim_policy": (
                "This map proves repository inspection, not software-engineering mastery. "
                "Every structural change still requires isolated validation."
            ),
        }
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.model_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(model, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.model_path)
        return self.status()

    def query(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        model = self.ensure()
        needle = query.strip().casefold()
        if not needle:
            return {"summary": model.get("summary", {}), "architecture": model.get("architecture")}
        matches = []
        for module in model.get("python_modules", []):
            searchable = json.dumps(module, ensure_ascii=False).casefold()
            if needle in searchable:
                matches.append(module)
            if len(matches) >= limit:
                break
        if len(matches) < limit:
            for item in model.get("files", []):
                if needle in str(item.get("path", "")).casefold():
                    matches.append(item)
                if len(matches) >= limit:
                    break
        return {
            "query": query,
            "matches": matches,
            "source_tree_sha256": model.get("source_tree_sha256"),
        }

    def impact(self, relative_path: str) -> dict[str, Any]:
        normalized = Path(relative_path.replace("\\", "/")).as_posix().lstrip("./")
        model = self.ensure()
        modules = [
            item
            for item in model.get("python_modules", [])
            if isinstance(item, dict)
        ]
        target = next((item for item in modules if item.get("path") == normalized), None)
        if target is None:
            raise KeyError(f"Unknown Python module in repository self-model: {normalized}")
        module_name = self._module_name(normalized)
        inbound = []
        tests = []
        for item in modules:
            imports = [str(value).lstrip(".") for value in item.get("imports", [])]
            if not any(
                value == module_name or value.startswith(f"{module_name}.")
                for value in imports
            ):
                continue
            path = str(item.get("path", ""))
            if item.get("kind") == "test":
                tests.append(path)
            else:
                inbound.append(path)
        inbound_names = {
            self._module_name(path)
            for path in inbound
            if path.startswith("src/") and path.endswith(".py")
        }
        for item in modules:
            if item.get("kind") != "test":
                continue
            imports = [str(value).lstrip(".") for value in item.get("imports", [])]
            if any(
                value == inbound_name or value.startswith(f"{inbound_name}.")
                for inbound_name in inbound_names
                for value in imports
            ):
                tests.append(str(item.get("path", "")))
        return {
            "path": normalized,
            "module": module_name,
            "definitions": target.get("definitions", []),
            "api_routes": target.get("api_routes", []),
            "outbound_imports": [
                value
                for value in target.get("imports", [])
                if str(value).lstrip(".").startswith("eck.")
            ],
            "inbound_modules": sorted(inbound),
            "direct_tests": sorted(set(tests)),
            "risk": {
                "inbound_module_count": len(inbound),
                "direct_test_count": len(set(tests)),
                "public_definition_count": len(target.get("definitions", [])),
                "requires_full_regression": True,
            },
            "source_tree_sha256": model.get("source_tree_sha256"),
        }

    def _source_files(self) -> list[Path]:
        files = []
        for current, directories, names in os.walk(self.project_root):
            directories[:] = [
                name for name in directories if name not in self._excluded_parts
            ]
            current_path = Path(current)
            for name in names:
                path = current_path / name
                if path.suffix.lower() not in self._text_suffixes and name not in {
                    "Dockerfile",
                    "LICENSE",
                }:
                    continue
                if path.stat().st_size > 2_000_000:
                    continue
                files.append(path)
        return sorted(files)

    def _python_module(self, path: Path, relative: str) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return {
                "path": relative,
                "kind": self._kind(relative),
                "definitions": [],
                "imports": [],
                "calls": [],
                "api_routes": [],
                "syntax_error": f"{exc.msg}:{exc.lineno}",
            }
        definitions = []
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions.append(
                    {
                        "name": node.name,
                        "type": type(node).__name__,
                        "line": node.lineno,
                    }
                )
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append("." * node.level + module)
        calls = sorted(
            {
                name
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                if (name := self._call_name(node.func))
            }
        )
        routes = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.lower()
                route = decorator.args[0]
                if method not in {"delete", "get", "patch", "post", "put"}:
                    continue
                if isinstance(route, ast.Constant) and isinstance(route.value, str):
                    routes.append(
                        {
                            "method": method.upper(),
                            "path": route.value,
                            "handler": node.name,
                            "line": node.lineno,
                        }
                    )
        return {
            "path": relative,
            "kind": self._kind(relative),
            "definitions": definitions,
            "imports": sorted(set(imports)),
            "calls": calls,
            "api_routes": sorted(routes, key=lambda item: (item["path"], item["method"])),
            "lines": len(text.splitlines()),
        }

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = RepositorySelfModelService._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return None

    @staticmethod
    def _module_name(relative: str) -> str:
        if not relative.startswith("src/") or not relative.endswith(".py"):
            return relative.replace("/", ".")
        module = relative[4:-3].replace("/", ".")
        return module.removesuffix(".__init__")

    @staticmethod
    def _kind(relative: str) -> str:
        if relative.startswith("tests/"):
            return "test"
        if relative.startswith("src/eck/"):
            return "core"
        if relative.startswith("docs/"):
            return "documentation"
        if relative.startswith("scripts/"):
            return "operations"
        if relative.startswith("config/"):
            return "configuration"
        return "project"

    @staticmethod
    def _architecture(modules: list[dict[str, Any]]) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        edges: Counter[tuple[str, str]] = Counter()
        for module in modules:
            path = str(module["path"])
            if not path.startswith("src/eck/"):
                continue
            parts = Path(path).parts
            source = parts[2] if len(parts) > 3 else "root"
            counts[source] += 1
            for imported in module.get("imports", []):
                normalized = str(imported).lstrip(".")
                if not normalized.startswith("eck."):
                    continue
                target = normalized.split(".", 2)[1]
                if target != source:
                    edges[(source, target)] += 1
        test_edges = []
        for module in modules:
            if module.get("kind") != "test":
                continue
            for imported in module.get("imports", []):
                normalized = str(imported).lstrip(".")
                if normalized.startswith("eck."):
                    test_edges.append(
                        {"test": module["path"], "target": normalized}
                    )
        return {
            "partitions": [
                {"name": name, "python_modules": count}
                for name, count in sorted(counts.items())
            ],
            "dependency_edges": [
                {"source": source, "target": target, "imports": count}
                for (source, target), count in sorted(edges.items())
            ],
            "test_edges": sorted(
                test_edges, key=lambda item: (str(item["target"]), str(item["test"]))
            ),
        }

    def _git_state(self) -> dict[str, Any]:
        root = self._git_root()
        if root is None:
            return {"available": False}
        common = ["git", "-c", f"safe.directory={root}", "-C", str(root)]
        try:
            commit = subprocess.run(
                [*common, "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            ).stdout.strip()
            status = subprocess.run(
                [*common, "status", "--porcelain"],
                capture_output=True,
                check=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            ).stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            return {"available": False, "root": str(root)}
        return {
            "available": True,
            "root": str(root),
            "commit": commit,
            "dirty": bool(status),
            "changed_paths": status[:100],
        }

    def _git_root(self) -> Path | None:
        for candidate in (self.project_root, *self.project_root.parents):
            if (candidate / ".git").exists():
                return candidate
        return None

    def _read(self) -> dict[str, Any]:
        value = json.loads(self.model_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Repository self-model must be a JSON object.")
        return value

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)
