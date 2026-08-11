from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.domain.enums import RuntimeSkillStatus
from eck.domain.models import RuntimeSkillManifest, RuntimeSkillRecord, SkillForgeRequest
from eck.events.bus import EventBus
from eck.modules.skills.lifecycle import RuntimeSkillTransitionPolicy
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
        self._worker_build_lock = asyncio.Lock()
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
            "automatic_repair_attempts": self.settings.skill_forge_max_repair_attempts,
            "canary_replays": self.settings.skill_canary_replays,
            "active": sum(item.status is RuntimeSkillStatus.ACTIVE for item in skills),
            "testing": sum(item.status is RuntimeSkillStatus.TESTING for item in skills),
            "draft": sum(item.status is RuntimeSkillStatus.DRAFT for item in skills),
            "failed": sum(item.status is RuntimeSkillStatus.FAILED for item in skills),
            "items": [item.model_dump(mode="json") for item in skills],
        }

    async def build_worker(self) -> dict[str, Any]:
        report = await self.ensure_worker_image(force=True)
        await self.events.publish(
            "SkillWorkerImageBuilt" if report["success"] else "SkillWorkerImageBuildFailed",
            self.settings.skill_worker_image,
            {"success": report["success"]},
        )
        if report["success"]:
            report["validated"] = await self.validate_pending()
        return report

    async def ensure_worker_image(self, *, force: bool = False) -> dict[str, Any]:
        async with self._worker_build_lock:
            if self.settings.environment == "test" and not force:
                health_method = getattr(self.worker.health, "__func__", None)
                if health_method is not DockerSkillWorker.health:
                    health = await self.worker.health()
                    if not health.get("available"):
                        return {
                            "success": False,
                            "detail": health.get("detail", "Docker is unavailable."),
                        }
                return {
                    "success": True,
                    "detail": "Skill worker image build is bypassed by the isolated test harness.",
                }
            health = await self.worker.health()
            if not health.get("available"):
                return {
                    "success": False,
                    "detail": health.get("detail", "Docker is unavailable."),
                }
            image = await self.worker.image_status()
            if image.get("available") and not force:
                return {
                    "success": True,
                    "detail": "Skill worker image is available.",
                    "image": image,
                }
            report = await self.worker.build_image(self.project_root)
            report["image"] = await self.worker.image_status()
            return report

    async def validate_pending(self) -> list[dict[str, Any]]:
        results = []
        latest_by_name: dict[str, RuntimeSkillRecord] = {}
        for skill in self.store.list_runtime_skills(limit=10000):
            latest_by_name.setdefault(skill.manifest.name, skill)
        for skill in reversed(list(latest_by_name.values())):
            if skill.status not in {
                RuntimeSkillStatus.DRAFT,
                RuntimeSkillStatus.FAILED,
                RuntimeSkillStatus.TESTING,
            }:
                continue
            results.append(await self.validate_skill(skill.runtime_skill_id))
        return results

    def security_report(self, skill: RuntimeSkillRecord) -> dict[str, Any]:
        """Re-run deterministic preflight checks for an already materialized skill."""
        source = Path(skill.source_dir).resolve()
        try:
            source.relative_to(self.generated_root)
            code = (source / "skill.py").read_text(encoding="utf-8")
            tests = (source / "test_skill.py").read_text(encoding="utf-8")
            ast.parse(code, filename="skill.py")
            ast.parse(tests, filename="test_skill.py")
            self._security_scan(code, skill.manifest.permissions)
            self._dependency_scan(code, skill.manifest.dependencies)
            self._security_scan(tests, ())
        except (OSError, SyntaxError, ValueError) as exc:
            return {
                "passed": False,
                "detail": f"{type(exc).__name__}: {exc}"[:1000],
                "acceptance_oracle": False,
            }
        return {
            "passed": True,
            "detail": "AST, permission, dependency, and generated-source checks passed.",
            "acceptance_oracle": self._is_acceptance_oracle(tests),
        }

    async def validate_skill(self, runtime_skill_id: str) -> dict[str, Any]:
        worker_image = await self.ensure_worker_image()
        if not worker_image.get("success"):
            skill = self._update_runtime_skill(
                runtime_skill_id,
                status=RuntimeSkillStatus.DRAFT,
                test_report={
                    "success": False,
                    "worker_unavailable": True,
                    "detail": str(worker_image.get("detail", ""))[-2000:],
                },
            )
            return skill.model_dump(mode="json")
        skill = self._update_runtime_skill(
            runtime_skill_id,
            status=RuntimeSkillStatus.TESTING,
        )
        report = await self.worker.validate(skill)
        unavailable = bool(report.get("worker_unavailable"))
        passed = bool(report.get("success"))
        canary_reports = [report]
        if passed and self.settings.skill_forge_auto_enable:
            for _ in range(1, self.settings.skill_canary_replays):
                if self.settings.skill_canary_delay_seconds:
                    await asyncio.sleep(self.settings.skill_canary_delay_seconds)
                replay = await self.worker.validate(skill)
                canary_reports.append(replay)
                unavailable = unavailable or bool(replay.get("worker_unavailable"))
                if not replay.get("success"):
                    passed = False
                    break
        report = {
            **report,
            "success": passed,
            "canary": {
                "required_replays": self.settings.skill_canary_replays,
                "completed_replays": len(canary_reports),
                "passed": passed and len(canary_reports) == self.settings.skill_canary_replays,
                "reports": [
                    {
                        "success": bool(item.get("success")),
                        "worker_unavailable": bool(item.get("worker_unavailable")),
                        "detail": str(item.get("detail", ""))[-1000:],
                        "test_output": str(item.get("test_output", ""))[-2000:],
                    }
                    for item in canary_reports
                ],
            },
        }
        prior_active = self.store.find_active_runtime_skill(skill.manifest.name)
        status = (
            RuntimeSkillStatus.DRAFT
            if unavailable
            else RuntimeSkillStatus.ACTIVE
            if passed and self.settings.skill_forge_auto_enable
            else RuntimeSkillStatus.FAILED
        )
        updated = self._update_runtime_skill(
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
        messages = [
                {
                    "role": "system",
                    "content": (
                        "你是 ECK 的隔離技能工程師。輸出 JSON，包含 code 與 tests。"
                        "程式會在無主機權限的 Docker 容器執行。entrypoint 必須提供 "
                        "execute(operation, payload, context)，tests 使用 pytest。"
                        "tests 必須從 skill 匯入 execute，不得假設函式已存在於全域命名空間。"
                        "先定義可觀察的輸入輸出契約，再實作與測試成功、邊界、惡意輸入及錯誤路徑。"
                        "測試不得依賴網路，外部 I/O 必須模擬；"
                        "不要用偶然的完整錯誤句子作為唯一斷言。"
                        "只能 import manifest dependencies 明列的第三方套件；dependencies "
                        "為空時只能使用 Python 標準庫，且不可把參考 GitHub 倉庫當套件匯入。"
                        "保持實作小於 180 行、測試小於 140 行，完整 JSON 必須在輸出預算內結束。"
                        "若提供 acceptance_examples，它們是不可修改的驗收真值；實作回傳值必須"
                        "與每筆 expected 完全相等，不得多出欄位。"
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
                            "dependency_policy": (
                                "Only Python standard-library imports are permitted when the "
                                "dependencies list is empty. Never invent requests, bs4, or other "
                                "third-party imports."
                            ),
                            "acceptance_examples": [
                                item.model_dump(mode="json")
                                for item in request.acceptance_examples
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        schema = {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "tests": {"type": "string"},
                    "improvements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["code", "improvements"],
        }
        parsed: dict[str, Any] = {}
        code = ""
        tests = ""
        last_error = "incomplete or malformed JSON"
        for _attempt in range(2):
            response = await self.brain.chat(
                messages,
                format_schema=schema,
                options={"num_predict": 4096, "think": False},
            )
            try:
                parsed = self._json_object(response.content)
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("code") and (
                request.acceptance_examples or parsed.get("tests")
            ):
                try:
                    code = self._ensure_execute_entrypoint(
                        self._clean_code(str(parsed["code"]))
                    )
                    tests = (
                        self._acceptance_tests(request.acceptance_examples)
                        if request.acceptance_examples
                        else self._ensure_test_import(
                            self._clean_code(str(parsed["tests"]))
                        )
                    )
                    self._validate_test_consistency(tests)
                    self._security_scan(code, request.permissions)
                    self._dependency_scan(code, request.dependencies)
                    self._security_scan(tests, ())
                    break
                except (SyntaxError, ValueError) as exc:
                    code = ""
                    tests = ""
                    last_error = str(exc)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Regenerate both complete files compactly. The prior candidate failed "
                        f"preflight: {last_error}. Do not explain. Tests may not assign different "
                        "expected outputs to identical inputs. Do not import a third-party module "
                        "unless it is explicitly listed in dependencies. Close every JSON string "
                        "and object."
                    ),
                }
            )
        if not code.strip() or not tests.strip():
            raise ValueError(
                "The model did not produce a preflight-valid skill after retry: "
                f"{last_error}"
            )
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
        self._retire_superseded_candidates(skill)
        await self.events.publish(
            "RuntimeSkillForged",
            skill.runtime_skill_id,
            {"name": manifest.name, "version": version, "model": response.model},
            correlation_id=skill.runtime_skill_id,
        )
        await self.validate_skill(skill.runtime_skill_id)
        current = self.store.get_runtime_skill(skill.runtime_skill_id)
        for _ in range(self.settings.skill_forge_max_repair_attempts):
            if current.status is not RuntimeSkillStatus.FAILED:
                break
            try:
                current = await self.repair_failed_skill(current.runtime_skill_id)
            except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
                await self.events.publish(
                    "RuntimeSkillRepairFailed",
                    current.runtime_skill_id,
                    {"name": current.manifest.name, "detail": str(exc)},
                    correlation_id=current.runtime_skill_id,
                )
                break
        return current

    async def repair_failed_skill(self, runtime_skill_id: str) -> RuntimeSkillRecord:
        failed = self.store.get_runtime_skill(runtime_skill_id)
        if failed.status is not RuntimeSkillStatus.FAILED:
            raise ValueError("Only a failed runtime skill can enter automatic repair.")
        source_dir = Path(failed.source_dir)
        prior_code = (source_dir / failed.manifest.entrypoint).read_text(encoding="utf-8")
        prior_tests = (source_dir / "test_skill.py").read_text(encoding="utf-8")
        versions = [
            item.manifest.version
            for item in self.store.list_runtime_skills(limit=10000)
            if item.manifest.name == failed.manifest.name
        ]
        version = self._next_version(versions)
        normalized_tests = self._ensure_test_import(prior_tests)
        acceptance_oracle = self._is_acceptance_oracle(prior_tests)
        if normalized_tests != prior_tests:
            return await self._persist_repair(
                failed,
                version,
                prior_code,
                normalized_tests,
                ("Added the missing generated-skill test import before model repair.",),
                generator="deterministic-framework-repair",
            )
        if acceptance_oracle:
            return await self._repair_acceptance_skill(
                failed,
                version,
                prior_code,
                prior_tests,
            )
        messages = [
                {
                    "role": "system",
                    "content": (
                        "你是 ECK 的隔離技能修復工程師。根據失敗測試報告修正 code 與 tests，"
                        "輸出 JSON。不得刪除有效測試來偽造通過，不得增加權限、主機存取、"
                        "tests 必須從 skill 匯入 execute。"
                        "先從 traceback 找到最小根因；安全拒絕若已成立，"
                        "不要只為匹配措辭而弱化實作。"
                        "若測試把非必要錯誤文案當契約，可保留安全行為並改成結構化狀態或錯誤類型斷言。"
                        "保持修復實作小於 180 行、測試小於 140 行並輸出完整閉合 JSON。"
                        "Docker socket、憑證存取或平台規則規避。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "name": failed.manifest.name,
                            "next_version": version,
                            "manifest": failed.manifest.model_dump(mode="json"),
                            "failed_test_report": failed.test_report,
                            "prior_code": prior_code,
                            "prior_tests": prior_tests,
                            "repair_requirement": (
                                "Implement the diagnosed change in code. Return values must equal "
                                "every expected object exactly, including returning no extra keys. "
                                "The acceptance-oracle tests are immutable. Returning "
                                "byte-identical code with only a written explanation is invalid. "
                                "Rewrite the whole implementation from the contract and evaluate "
                                "every acceptance case before returning code; do not apply a "
                                "narrow one-line patch for only the first failure."
                                if acceptance_oracle
                                else "Implement the diagnosed change in code or tests. Returning "
                                "byte-identical files with only a written explanation is invalid."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        schema = {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "tests": {"type": "string"},
                    "improvements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["code", "improvements"],
        }
        parsed: dict[str, Any] = {}
        code = ""
        tests = ""
        last_error = "incomplete or malformed JSON"
        for _ in range(2):
            response = await self.brain.chat(
                messages,
                format_schema=schema,
                options={"num_predict": 4096, "think": False},
            )
            try:
                parsed = self._json_object(response.content)
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("code") and (acceptance_oracle or parsed.get("tests")):
                try:
                    code = self._ensure_execute_entrypoint(
                        self._clean_code(str(parsed["code"]))
                    )
                    tests = (
                        prior_tests
                        if acceptance_oracle
                        else self._ensure_test_import(
                            self._clean_code(str(parsed["tests"]))
                        )
                    )
                    self._validate_test_consistency(tests)
                    if code == prior_code and tests == normalized_tests:
                        raise ValueError(
                            "The repair returned byte-identical code and tests."
                        )
                    self._security_scan(code, failed.manifest.permissions)
                    self._dependency_scan(code, failed.manifest.dependencies)
                    self._security_scan(tests, ())
                    break
                except (SyntaxError, ValueError) as exc:
                    code = ""
                    tests = ""
                    last_error = str(exc)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Return compact complete repaired code and tests. The prior candidate "
                        f"failed preflight: {last_error}. Keep inputs and expected outputs "
                        "internally consistent. Use valid escaped JSON strings and no explanation."
                    ),
                }
            )
        if not code.strip() or not tests.strip():
            raise ValueError(
                "The model did not produce a preflight-valid repair after retry: "
                f"{last_error}"
            )
        improvements = tuple(
            str(item)[:500]
            for item in parsed.get("improvements", [])
            if str(item).strip()
        ) or (f"Automatic repair of failed version {failed.manifest.version}.",)
        return await self._persist_repair(
            failed,
            version,
            code,
            tests,
            improvements,
            generator=response.model,
        )

    async def _repair_acceptance_skill(
        self,
        failed: RuntimeSkillRecord,
        version: str,
        prior_code: str,
        oracle_tests: str,
    ) -> RuntimeSkillRecord:
        examples = self._acceptance_cases_from_tests(oracle_tests)
        example_contract = "\n".join(
            f"{index}. execute({item['operation']!r}, {item['payload']!r}, "
            f"{item.get('context', {})!r}) MUST return exactly {item['expected']!r}"
            for index, item in enumerate(examples, start=1)
        )
        exact_exit_outputs = {
            repr(item["expected"])
            for item in examples
            if isinstance(item.get("expected"), dict)
            and len(item["expected"]) == 1
            and "status" in item["expected"]
        }
        oracle_note = (
            "Inputs with these one-key expected outputs are independent immediate-return "
            "conditions, even when other payload fields are non-empty: "
            f"{sorted(exact_exit_outputs)!r}."
            if exact_exit_outputs
            else "Every expected output is exact and independent."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Return only complete Python code, without Markdown or explanation. Rewrite "
                    "one small skill from scratch, define execute(operation, payload, context), "
                    "and make every return value exactly equal to expected, with no extra keys."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"OBJECTIVE:\n{failed.manifest.description}\n\n"
                    f"IMMUTABLE EXAMPLES:\n{example_contract}\n\n"
                    f"ORACLE NOTE:\n{oracle_note}\n\n"
                    "Write from scratch. Before answering, check every numbered example exactly."
                ),
            },
        ]
        last_error = "incomplete Python code"
        for _attempt in range(2):
            response = await self.brain.chat(
                messages,
                options={"num_predict": 2048, "think": False, "temperature": 0},
            )
            try:
                code = self._ensure_execute_entrypoint(
                    self._clean_code(response.content)
                )
                if code == prior_code:
                    raise ValueError("The repair returned byte-identical code.")
                self._security_scan(code, failed.manifest.permissions)
                self._dependency_scan(code, failed.manifest.dependencies)
            except (json.JSONDecodeError, SyntaxError, ValueError) as exc:
                last_error = str(exc)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Retry with complete compact Python. Preflight failed: {last_error}. "
                            "Return only Python and re-evaluate every immutable example."
                        ),
                    }
                )
                continue
            return await self._persist_repair(
                failed,
                version,
                code,
                oracle_tests,
                (f"Repaired against operator oracle from {failed.manifest.version}.",),
                generator=response.model,
            )
        raise ValueError(
            "The model did not produce a preflight-valid oracle repair after retry: "
            f"{last_error}"
        )

    async def _persist_repair(
        self,
        failed: RuntimeSkillRecord,
        version: str,
        code: str,
        tests: str,
        improvements: tuple[str, ...],
        *,
        generator: str,
    ) -> RuntimeSkillRecord:
        manifest = failed.manifest.model_copy(update={"version": version})
        repaired_dir = self.generated_root / manifest.name / version
        repaired_dir.mkdir(parents=True, exist_ok=False)
        (repaired_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        (repaired_dir / manifest.entrypoint).write_text(code, encoding="utf-8")
        (repaired_dir / "test_skill.py").write_text(tests, encoding="utf-8")
        repaired = self.store.add_runtime_skill(
            manifest,
            source_dir=str(repaired_dir),
            source="eck-generated",
            status=RuntimeSkillStatus.DRAFT,
            improvements=improvements,
        )
        self._retire_superseded_candidates(repaired)
        await self.events.publish(
            "RuntimeSkillRepairForged",
            repaired.runtime_skill_id,
            {
                "name": manifest.name,
                "version": version,
                "repaired_from": failed.runtime_skill_id,
                "model": generator,
            },
            correlation_id=repaired.runtime_skill_id,
        )
        await self.validate_skill(repaired.runtime_skill_id)
        return self.store.get_runtime_skill(repaired.runtime_skill_id)

    def _retire_superseded_candidates(self, current: RuntimeSkillRecord) -> None:
        for item in self.store.list_runtime_skills(limit=10000):
            if (
                item.runtime_skill_id != current.runtime_skill_id
                and item.manifest.name == current.manifest.name
                and item.status
                in {
                    RuntimeSkillStatus.DRAFT,
                    RuntimeSkillStatus.TESTING,
                    RuntimeSkillStatus.FAILED,
                }
            ):
                self._update_runtime_skill(
                    item.runtime_skill_id,
                    status=RuntimeSkillStatus.RETIRED,
                )

    def _update_runtime_skill(
        self,
        runtime_skill_id: str,
        *,
        status: RuntimeSkillStatus,
        test_report: dict[str, Any] | None = None,
        activate: bool = False,
    ) -> RuntimeSkillRecord:
        current = self.store.get_runtime_skill(runtime_skill_id)
        RuntimeSkillTransitionPolicy.require(current.status, status)
        return self.store.update_runtime_skill(
            runtime_skill_id,
            status=status,
            test_report=test_report,
            activate=activate,
        )

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
    def _ensure_test_import(tests: str) -> str:
        tree = ast.parse(tests)
        imports_execute = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "skill"
            and any(alias.name == "execute" for alias in node.names)
            for node in tree.body
        )
        if imports_execute:
            return tests
        return f"from skill import execute\n\n{tests}"

    @staticmethod
    def _ensure_execute_entrypoint(code: str) -> str:
        code = re.sub(
            r"(?m)^from\s+skill\s+import\s+execute\s*$\n?",
            "",
            code,
        )
        tree = ast.parse(code)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if any(node.name == "execute" for node in functions):
            return code
        compatible = [
            node
            for node in functions
            if not node.name.startswith("_")
            and len(node.args.posonlyargs) + len(node.args.args) >= 3
        ]
        if len(compatible) != 1:
            raise ValueError(
                "Generated skill must define execute(operation, payload, context) or one "
                "unambiguous compatible function."
            )
        target = compatible[0].name
        return (
            f"{code.rstrip()}\n\n\n"
            "def execute(operation, payload, context):\n"
            f"    return {target}(operation, payload, context)\n"
        )

    @staticmethod
    def _validate_test_consistency(tests: str) -> None:
        tree = ast.parse(tests)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            function = node.func
            if not isinstance(function, ast.Attribute) or function.attr != "parametrize":
                continue
            try:
                parameter_names = [
                    item.strip()
                    for item in ast.literal_eval(node.args[0]).split(",")
                ]
                cases = ast.literal_eval(node.args[1])
            except (SyntaxError, ValueError, TypeError):
                continue
            if len(parameter_names) < 2 or not isinstance(cases, (list, tuple)):
                continue
            observed: dict[str, str] = {}
            for case in cases:
                if not isinstance(case, (list, tuple)) or len(case) != len(parameter_names):
                    continue
                input_key = json.dumps(case[:-1], sort_keys=True, default=str)
                expected = json.dumps(case[-1], sort_keys=True, default=str)
                prior = observed.setdefault(input_key, expected)
                if prior != expected:
                    raise ValueError(
                        "Contradictory parameterized tests assign different expected outputs "
                        "to identical inputs."
                    )

    @staticmethod
    def _acceptance_tests(examples: tuple[Any, ...]) -> str:
        cases = [item.model_dump(mode="json") for item in examples]
        serialized = json.dumps(cases, ensure_ascii=False, sort_keys=True)
        return (
            "# eck-acceptance-oracle:v1\n"
            "import json\n\n"
            "import pytest\n\n"
            "from skill import execute\n\n"
            f"CASES = json.loads({serialized!r})\n\n"
            "@pytest.mark.parametrize('case', CASES)\n"
            "def test_operator_acceptance_examples(case):\n"
            "    actual = execute(\n"
            "        case['operation'], case['payload'], case.get('context', {})\n"
            "    )\n"
            "    assert actual == case['expected']\n"
        )

    @staticmethod
    def _is_acceptance_oracle(tests: str) -> bool:
        return tests.startswith("# eck-acceptance-oracle:v1\n")

    @staticmethod
    def _acceptance_cases_from_tests(tests: str) -> list[dict[str, Any]]:
        tree = ast.parse(tests)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "CASES"
                for target in node.targets
            ):
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "json"
                and value.func.attr == "loads"
                and value.args
            ):
                parsed = json.loads(ast.literal_eval(value.args[0]))
                if isinstance(parsed, list) and all(
                    isinstance(item, dict) for item in parsed
                ):
                    return parsed
        raise ValueError("The immutable acceptance examples could not be recovered.")

    @staticmethod
    def _dependency_scan(code: str, dependencies: tuple[str, ...]) -> None:
        aliases = {
            "beautifulsoup4": "bs4",
            "pillow": "PIL",
            "python-docx": "docx",
            "python-pptx": "pptx",
            "pyyaml": "yaml",
        }
        declared = {
            aliases.get(name, name.replace("-", "_"))
            for value in dependencies
            if (name := re.split(r"[<>=!~\[]", value, maxsplit=1)[0].casefold())
        }
        imports: set[str] = set()
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        undeclared = sorted(
            name
            for name in imports
            if name not in sys.stdlib_module_names
            and name.casefold() not in {item.casefold() for item in declared}
        )
        if undeclared:
            raise ValueError(
                "Generated skill imports undeclared third-party modules: "
                + ", ".join(undeclared)
            )

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
