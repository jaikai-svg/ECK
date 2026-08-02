from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import RuntimeSkillManifest, RuntimeSkillRecord, SkillForgeRequest
from eck.events.bus import EventBus
from eck.runtime.worker import DockerSkillWorker
from eck.services.versioning import VersionService
from eck.storage.sqlite import SQLiteStore


class SkillForgeService:
    _foundation = (
        RuntimeSkillManifest(
            name="browser.explore",
            version="1.0.0",
            description="Inspect and capture public webpages with Playwright in Docker.",
            category="browser",
            entrypoint="foundation.py",
            permissions=("network:public", "artifact:write"),
            operations=("inspect", "screenshot"),
        ),
        RuntimeSkillManifest(
            name="document.create",
            version="1.0.0",
            description="Create DOCX, PDF, PPTX, and XLSX artifacts in Docker.",
            category="documents",
            entrypoint="foundation.py",
            permissions=("artifact:write",),
            operations=("create",),
        ),
        RuntimeSkillManifest(
            name="image.process",
            version="1.0.0",
            description="Create and transform image artifacts in Docker.",
            category="images",
            entrypoint="foundation.py",
            permissions=("artifact:write",),
            operations=("create",),
        ),
        RuntimeSkillManifest(
            name="code.sandbox",
            version="1.0.0",
            description="Build and test generated Python or Node projects inside Docker.",
            category="development",
            entrypoint="foundation.py",
            permissions=("artifact:write", "code:execute"),
            operations=("test",),
        ),
        RuntimeSkillManifest(
            name="data.advanced",
            version="1.0.0",
            description="Analyze structured datasets and emit reproducible artifacts.",
            category="data",
            entrypoint="foundation.py",
            permissions=("artifact:write",),
            operations=("analyze",),
        ),
        RuntimeSkillManifest(
            name="social.connector",
            version="1.0.0",
            description="Enforce platform automation policy before official social adapters run.",
            category="social",
            entrypoint="foundation.py",
            permissions=("network:public", "public-action:policy-gated"),
            operations=("policy_check", "publish", "reply", "like", "follow"),
        ),
    )

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        brain: BrainProvider,
        worker: DockerSkillWorker,
        versions: VersionService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.brain = brain
        self.worker = worker
        self.versions = versions
        self.project_root = Path(__file__).resolve().parents[3]
        self.generated_root = (settings.workspace_dir / "runtime_skills").resolve()
        self.generated_root.mkdir(parents=True, exist_ok=True)
        self.seed_foundation_pack()

    def seed_foundation_pack(self) -> None:
        existing = {
            (item.manifest.name, item.manifest.version)
            for item in self.store.list_runtime_skills(limit=10000)
        }
        source_dir = self.project_root / "docker" / "skill-worker" / "foundation"
        for manifest in self._foundation:
            if (manifest.name, manifest.version) in existing:
                continue
            self.store.add_runtime_skill(
                manifest,
                source_dir=str(source_dir),
                source="foundation",
                improvements=("Initial isolated foundation implementation.",),
            )

    async def status(self) -> dict[str, Any]:
        health = await self.worker.health()
        skills = self.store.list_runtime_skills(limit=10000)
        return {
            "worker": health,
            "auto_enable": self.settings.skill_forge_auto_enable,
            "dependency_install": self.settings.skill_dependency_install_enabled,
            "active": sum(item.status is RuntimeSkillStatus.ACTIVE for item in skills),
            "testing": sum(item.status is RuntimeSkillStatus.TESTING for item in skills),
            "draft": sum(item.status is RuntimeSkillStatus.DRAFT for item in skills),
            "failed": sum(item.status is RuntimeSkillStatus.FAILED for item in skills),
            "items": [item.model_dump(mode="json") for item in skills],
        }

    async def build_worker(self) -> dict[str, Any]:
        report = await self.worker.build_image(self.project_root)
        await self.events.publish(
            "SkillWorkerImageBuilt" if report["success"] else "SkillWorkerImageBuildFailed",
            self.settings.skill_worker_image,
            {"success": report["success"]},
        )
        if report["success"]:
            report["validated"] = await self.validate_pending()
        return report

    async def validate_pending(self) -> list[dict[str, Any]]:
        results = []
        for skill in reversed(self.store.list_runtime_skills(limit=10000)):
            if skill.status not in {
                RuntimeSkillStatus.DRAFT,
                RuntimeSkillStatus.FAILED,
                RuntimeSkillStatus.TESTING,
            }:
                continue
            results.append(await self.validate_skill(skill.runtime_skill_id))
        return results

    async def validate_skill(self, runtime_skill_id: str) -> dict[str, Any]:
        skill = self.store.update_runtime_skill(
            runtime_skill_id,
            status=RuntimeSkillStatus.TESTING,
        )
        report = await self.worker.validate(skill)
        unavailable = bool(report.get("worker_unavailable"))
        passed = bool(report.get("success"))
        prior_active = self.store.find_active_runtime_skill(skill.manifest.name)
        status = (
            RuntimeSkillStatus.DRAFT
            if unavailable
            else RuntimeSkillStatus.ACTIVE
            if passed and self.settings.skill_forge_auto_enable
            else RuntimeSkillStatus.FAILED
        )
        updated = self.store.update_runtime_skill(
            runtime_skill_id,
            status=status,
            test_report=report,
            activate=status is RuntimeSkillStatus.ACTIVE,
        )
        event_type = (
            "RuntimeSkillActivated"
            if status is RuntimeSkillStatus.ACTIVE
            else "RuntimeSkillTested"
        )
        await self.events.publish(
            event_type,
            runtime_skill_id,
            {
                "name": updated.manifest.name,
                "version": updated.manifest.version,
                "status": updated.status.value,
            },
            correlation_id=runtime_skill_id,
        )
        if status is RuntimeSkillStatus.ACTIVE:
            if prior_active is not None and prior_active.runtime_skill_id != runtime_skill_id:
                self.versions.record_runtime_update(
                    f"Runtime skill {skill.manifest.name} improved to {skill.manifest.version}."
                )
            else:
                await self.versions.observe_verified_skills()
        return updated.model_dump(mode="json")

    async def forge(self, request: SkillForgeRequest) -> RuntimeSkillRecord:
        versions = [
            item.manifest.version
            for item in self.store.list_runtime_skills(limit=10000)
            if item.manifest.name == request.name
        ]
        version = self._next_version(versions)
        response = await self.brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 ECK 的隔離技能工程師。輸出 JSON，包含 code 與 tests。"
                        "程式會在無主機權限的 Docker 容器執行。entrypoint 必須提供 "
                        "execute(operation, payload, context)，tests 使用 pytest。"
                        "不可存取 Docker socket、主機路徑、憑證或規避平台規則。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "name": request.name,
                            "version": version,
                            "objective": request.objective,
                            "operations": request.operations,
                            "permissions": request.permissions,
                            "dependencies": request.dependencies,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            format_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "tests": {"type": "string"},
                    "improvements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["code", "tests", "improvements"],
            },
        )
        parsed = self._json_object(response.content)
        code = self._clean_code(str(parsed.get("code", "")))
        tests = self._clean_code(str(parsed.get("tests", "")))
        if not code or not tests:
            raise ValueError("The model did not produce executable skill code and tests.")
        self._security_scan(code, request.permissions)
        self._security_scan(tests, ())
        manifest = RuntimeSkillManifest(
            name=request.name,
            version=version,
            description=request.objective[:1000],
            category=request.category,
            permissions=request.permissions,
            dependencies=(
                request.dependencies if self.settings.skill_dependency_install_enabled else ()
            ),
            operations=request.operations,
            generated=True,
        )
        source_dir = self.generated_root / request.name / version
        source_dir.mkdir(parents=True, exist_ok=False)
        (source_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        (source_dir / "skill.py").write_text(code, encoding="utf-8")
        (source_dir / "test_skill.py").write_text(tests, encoding="utf-8")
        improvements = tuple(
            str(item)[:500] for item in parsed.get("improvements", []) if str(item).strip()
        ) or (request.objective[:500],)
        skill = self.store.add_runtime_skill(
            manifest,
            source_dir=str(source_dir),
            source="eck-generated",
            status=RuntimeSkillStatus.DRAFT,
            improvements=improvements,
        )
        await self.events.publish(
            "RuntimeSkillForged",
            skill.runtime_skill_id,
            {"name": manifest.name, "version": version, "model": response.model},
            correlation_id=skill.runtime_skill_id,
        )
        await self.validate_skill(skill.runtime_skill_id)
        return self.store.get_runtime_skill(skill.runtime_skill_id)

    @staticmethod
    def _next_version(versions: list[str]) -> str:
        if not versions:
            return "0.1.0"
        parsed = sorted(tuple(int(part) for part in value.split(".")) for value in versions)
        major, minor, patch = parsed[-1]
        return f"{major}.{minor}.{patch + 1}"

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                return {}
            value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _clean_code(value: str) -> str:
        return re.sub(r"^```(?:python)?\s*|\s*```$", "", value.strip(), flags=re.I) + "\n"

    @staticmethod
    def _security_scan(code: str, permissions: tuple[str, ...]) -> None:
        tree = ast.parse(code)
        blocked_imports = {"ctypes", "winreg"}
        if "code:execute" not in permissions:
            blocked_imports.add("subprocess")
        if "network:public" not in permissions:
            blocked_imports.add("socket")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [item.name.split(".")[0] for item in node.names]
                    if isinstance(node, ast.Import)
                    else [str(node.module).split(".")[0]]
                )
                if blocked_imports.intersection(names):
                    raise ValueError(f"Blocked import in generated skill: {names}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec", "compile", "__import__"}
            ):
                raise ValueError(f"Blocked dynamic execution primitive: {node.func.id}")
