from __future__ import annotations

import ast
import builtins
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from eck.core.time import utc_now
from eck.services.project_lab_components.base import ProjectLabMixinBase


class ProjectLabValidationMixin(ProjectLabMixinBase):
    @staticmethod
    def _clear_source_dir(project_dir: Path, source_dir: Path) -> None:
        project_root = project_dir.resolve()
        source_root = source_dir.resolve()
        if source_root.parent != project_root or source_root.name != "source":
            raise ValueError("Autonomous project source directory escaped its project root.")
        for path in source_root.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def _validate_files(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            raise ValueError("Project model returned no files.")
        files: list[dict[str, str]] = []
        total_bytes = 0
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Project file entry must be an object.")
            path = self._safe_relative_path(str(item.get("path", "")))
            content = str(item.get("content", ""))
            if path in seen:
                raise ValueError(f"Duplicate autonomous project file: {path}")
            if not content:
                if path.casefold() == "requirements.txt":
                    continue
                raise ValueError(f"Empty autonomous project file: {path}")
            if not path.endswith((".md", ".py", ".toml", ".txt", ".json", ".yaml", ".yml")):
                raise ValueError(f"Unsupported autonomous project file type: {path}")
            seen.add(path)
            total_bytes += len(content.encode("utf-8"))
            files.append({"path": path, "content": content})
        if len(files) > 30 or total_bytes > 500_000:
            raise ValueError("Autonomous project exceeds the file or byte limit.")
        if not any(item["path"].startswith("tests/test_") for item in files):
            raise ValueError("Autonomous project must include deterministic pytest tests.")
        if not any(
            item["path"].endswith(".py") and not item["path"].startswith("tests/")
            for item in files
        ):
            raise ValueError("Autonomous project must include executable Python source.")
        return files

    @staticmethod
    def _write_project_files(source_dir: Path, files: list[dict[str, str]]) -> None:
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")

    @staticmethod
    def _ensure_disclosure(
        source_dir: Path,
        objective: str,
        research: list[dict[str, Any]],
    ) -> None:
        readme = source_dir / "README.md"
        existing = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        disclosure = (
            "# AI/ECK Autonomous Project\n\n"
            "This project was autonomously drafted by ECK, tested in an isolated local worker, "
            "and published with explicit AI/ECK disclosure. Human review is still recommended.\n\n"
            f"## Objective\n\n{objective}\n\n"
            "## Research Evidence\n\n"
            f"{len(research)} verified ECK research runs informed this prototype.\n\n"
        )
        if "AI/ECK" not in existing:
            readme.write_text(disclosure + existing, encoding="utf-8")

    def _scan_secrets(self, source_dir: Path) -> None:
        for path in source_dir.rglob("*"):
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(text) for pattern in self._secret_patterns):
                raise ValueError(f"Potential secret detected in autonomous project: {path.name}")

    @staticmethod
    def _static_quality_gate(
        source_dir: Path,
        *,
        objective: str | None = None,
    ) -> dict[str, Any]:
        issues: list[str] = []
        source_functions = 0
        behavioral_assertions = 0
        weak_assertions = {"assertIsInstance", "assertIsNotNone", "assertTrue"}
        placeholder_phrases = {
            "optimization successful",
            "simulate quantization",
            "simulated measurement",
            "placeholder",
            "not implemented",
            "dummy result",
        }
        local_modules = {
            path.stem
            for path in source_dir.rglob("*.py")
            if not path.relative_to(source_dir).as_posix().startswith("tests/")
        }
        allowed_imports = set(sys.stdlib_module_names) | local_modules | {"pytest"}
        identifier_terms = {
            token
            for path in source_dir.rglob("*.py")
            for token in re.findall(
                r"[a-z][a-z0-9]{2,}",
                path.relative_to(source_dir).as_posix().casefold().replace("_", " "),
            )
        }
        for path in sorted(source_dir.rglob("*.py")):
            relative = path.relative_to(source_dir).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text, filename=relative)
            except SyntaxError as exc:
                issues.append(f"Python syntax error in {relative}: line {exc.lineno}.")
                continue
            is_test = relative.startswith("tests/test_")
            module_defined = set(dir(builtins))
            for top_level in tree.body:
                if isinstance(top_level, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    module_defined.add(top_level.name)
                elif isinstance(top_level, ast.Import):
                    module_defined.update(
                        alias.asname or alias.name.split(".", maxsplit=1)[0]
                        for alias in top_level.names
                    )
                elif isinstance(top_level, ast.ImportFrom):
                    module_defined.update(alias.asname or alias.name for alias in top_level.names)
                elif isinstance(top_level, (ast.Assign, ast.AnnAssign)):
                    module_defined.update(
                        name.id
                        for name in ast.walk(top_level)
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                    )
            for node in ast.walk(tree):
                if isinstance(
                    node,
                    (ast.Name, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    identifier = node.id if isinstance(node, ast.Name) else node.name
                    identifier_terms.update(
                        re.findall(
                            r"[a-z][a-z0-9]{2,}",
                            re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier)
                            .casefold()
                            .replace("_", " "),
                        )
                    )
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom):
                        names = (
                            {node.module.split(".", maxsplit=1)[0]} if node.module else set()
                        )
                    else:
                        names = {
                            alias.name.split(".", maxsplit=1)[0] for alias in node.names
                        }
                    external = names - allowed_imports
                    if external:
                        issues.append(
                            f"Non-standard dependency in {relative}: {', '.join(sorted(external))}."
                        )
                    if names & {"random", "secrets", "time"}:
                        issues.append(f"Non-deterministic import in {relative}.")
                    if is_test and (
                        "unittest.mock" in text
                        or names & {"mock", "unittest.mock"}
                    ):
                        issues.append("Tests may not mock or patch the implementation under test.")
                if not is_test and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    source_functions += 1
                    body = [
                        item
                        for item in node.body
                        if not (
                            isinstance(item, ast.Expr)
                            and isinstance(item.value, ast.Constant)
                            and isinstance(item.value.value, str)
                        )
                    ]
                    if len(body) == 1 and (
                        isinstance(body[0], ast.Pass)
                        or (
                            isinstance(body[0], ast.Return)
                            and isinstance(body[0].value, ast.Constant)
                        )
                    ):
                        issues.append(f"Trivial constant function {node.name} in {relative}.")
                    local_defined = {
                        argument.arg
                        for argument in (
                            list(node.args.posonlyargs)
                            + list(node.args.args)
                            + list(node.args.kwonlyargs)
                        )
                    }
                    if node.args.vararg:
                        local_defined.add(node.args.vararg.arg)
                    if node.args.kwarg:
                        local_defined.add(node.args.kwarg.arg)
                    local_defined.update(
                        name.id
                        for name in ast.walk(node)
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                    )
                    undefined = {
                        name.id
                        for name in ast.walk(node)
                        if isinstance(name, ast.Name)
                        and isinstance(name.ctx, ast.Load)
                        and name.id not in module_defined
                        and name.id not in local_defined
                    }
                    if undefined:
                        issues.append(
                            f"Undefined names in {relative}:{node.name}: "
                            f"{', '.join(sorted(undefined))}."
                        )
                if is_test and isinstance(node, ast.Assert):
                    behavioral_assertions += 1
                if (
                    is_test
                    and isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("assert")
                    and node.func.attr not in weak_assertions
                ):
                    behavioral_assertions += 1
            lowered = text.casefold()
            if any(phrase in lowered for phrase in placeholder_phrases):
                issues.append(f"Placeholder or simulated success text in {relative}.")
        if source_functions == 0:
            issues.append("No executable source function was found.")
        if behavioral_assertions < 2:
            issues.append("Tests require at least two behavioral assertions.")
        objective_keywords: set[str] = set()
        matched_keywords: set[str] = set()
        if objective:
            focus = objective.split("Lead topic:", maxsplit=1)[-1]
            stopwords = {
                "build",
                "small",
                "reproducible",
                "local",
                "python",
                "project",
                "turns",
                "supplied",
                "verified",
                "research",
                "executable",
                "experiment",
                "current",
                "evidence",
                "credibility",
                "methods",
                "measurement",
                "reproducibility",
            }
            objective_keywords = {
                token
                for token in re.findall(r"[a-z][a-z0-9]{3,}", focus.casefold())
                if token not in stopwords and not token.isdigit()
            }
            matched_keywords = objective_keywords & identifier_terms
            if len(objective_keywords) >= 2 and len(matched_keywords) < 2:
                issues.append(
                    "Implementation identifiers are not relevant to the objective; matched "
                    f"{', '.join(sorted(matched_keywords)) or 'none'}."
                )
        return {
            "success": not issues,
            "issues": sorted(set(issues)),
            "source_functions": source_functions,
            "behavioral_assertions": behavioral_assertions,
            "objective_keywords": sorted(objective_keywords),
            "matched_objective_keywords": sorted(matched_keywords),
        }

    async def _audit_verified_projects(self) -> None:
        for manifest in self.list_projects():
            status = manifest.get("status")
            validation = manifest.get("validation", {})
            if status not in {"verified", "quality_rejected"}:
                continue
            if status == "quality_rejected" and not validation.get("success"):
                continue
            source_dir = Path(str(manifest.get("source_dir", "")))
            quality = (
                self._static_quality_gate(
                    source_dir,
                    objective=str(manifest.get("objective", "")),
                )
                if source_dir.is_dir()
                else {"success": False, "issues": ["Project source directory is missing."]}
            )
            if quality["success"]:
                continue
            manifest["status"] = "quality_rejected"
            validation = manifest.setdefault("validation", {})
            validation["isolated_test_success"] = bool(validation.get("success"))
            validation["success"] = False
            validation["post_audit"] = quality
            manifest["updated_at"] = utc_now().isoformat()
            self._write_manifest(
                self._project_dir(str(manifest["project_id"])),
                manifest,
            )
            if status == "verified":
                await self.events.publish(
                    "AutonomousProjectQualityRejected",
                    str(manifest["project_id"]),
                    {"issues": quality["issues"]},
                    correlation_id=str(manifest["project_id"]),
                )
            state = self._read_json(self.state_path) if self.state_path.is_file() else {}
            if state.get("project_id") == manifest["project_id"]:
                await self._record_cycle(
                    "quality_rejected",
                    (
                        f"Autonomous project {manifest['project_id']} was rejected by the "
                        "current deterministic quality contract."
                    ),
                    project_id=str(manifest["project_id"]),
                )

    async def _validate_in_docker(self, source_dir: Path) -> dict[str, Any]:
        image_status = await self.worker.image_status()
        if not image_status.get("available"):
            return {"success": False, "detail": image_status.get("detail", "Worker unavailable.")}
        executable = str(image_status["executable"])
        command = [
            executable,
            "run",
            "--rm",
            "--read-only",
            "--network",
            "none",
            "--memory",
            f"{self.settings.skill_worker_memory_mb}m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=256m,mode=1777",
            "--mount",
            f"type=bind,source={source_dir.resolve()},target=/project,readonly",
            "--workdir",
            "/project",
            "--entrypoint",
            "python",
            self.settings.skill_worker_image,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        result = await self._run_process(
            command,
            cwd=source_dir,
            timeout=self.settings.skill_worker_timeout_seconds,
        )
        return {
            "success": result["returncode"] == 0,
            "test_command": [
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            "returncode": result["returncode"],
            "output_tail": result["output_tail"],
            "isolated": True,
            "network": "none",
        }

