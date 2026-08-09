from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from eck.brain.base import BrainProvider
from eck.core.time import utc_now
from eck.domain.enums import BenchmarkSuite, RuntimeSkillStatus
from eck.domain.models import (
    BenchmarkRunCreate,
    BenchmarkRunRecord,
    ObjectiveEvaluationRequest,
)
from eck.events.bus import EventBus
from eck.runtime.resources import SystemResourceMonitor
from eck.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class ObjectiveCase:
    case_id: str
    category: str
    prompt: str
    allowed_answers: tuple[str, ...]
    expected: str


OBJECTIVE_CASES: tuple[ObjectiveCase, ...] = (
    ObjectiveCase(
        "reasoning-01",
        "reasoning",
        "計算 17 × 19。",
        ("323",),
        "323",
    ),
    ObjectiveCase(
        "reasoning-02",
        "reasoning",
        "一次初始嘗試失敗後最多重試 3 次，最多總共會執行幾次？",
        ("4",),
        "4",
    ),
    ObjectiveCase(
        "reasoning-03",
        "reasoning",
        "所有啟用技能都必須有通過的測試；Alpha 沒有通過測試。Alpha 能啟用嗎？",
        ("yes", "no"),
        "no",
    ),
    ObjectiveCase(
        "reasoning-04",
        "reasoning",
        "數列 2, 6, 12, 20, 30 的下一項是什麼？",
        ("42",),
        "42",
    ),
    ObjectiveCase(
        "reasoning-05",
        "reasoning",
        "A 依賴 B，B 依賴 C，C 不依賴 A。此依賴圖是否有循環？",
        ("cyclic", "acyclic"),
        "acyclic",
    ),
    ObjectiveCase(
        "evidence-01",
        "evidence",
        "主張效能提高約 12%；兩個獨立、方法公開的資料集分別量到 11% 與 13%。",
        ("supported", "contradicted", "insufficient"),
        "supported",
    ),
    ObjectiveCase(
        "evidence-02",
        "evidence",
        "主張某套件安全；唯一依據是匿名社群貼文，沒有版本、測試或漏洞資料。",
        ("supported", "contradicted", "insufficient"),
        "insufficient",
    ),
    ObjectiveCase(
        "evidence-03",
        "evidence",
        "主張更新後延遲下降；兩次可重現測試都顯示延遲上升。",
        ("supported", "contradicted", "insufficient"),
        "contradicted",
    ),
    ObjectiveCase(
        "evidence-04",
        "evidence",
        "文章引用一份無法取得的報告，沒有提供資料、方法或第二來源。",
        ("supported", "contradicted", "insufficient"),
        "insufficient",
    ),
    ObjectiveCase(
        "evidence-05",
        "evidence",
        "官方 release notes 與同版本原始碼 tag 都記錄相同 API 變更。",
        ("supported", "contradicted", "insufficient"),
        "supported",
    ),
    ObjectiveCase(
        "routing-01",
        "tool_routing",
        "檢查本機 Git 工作區的狀態與差異。",
        ("git.workspace", "web.public_explore", "artifact.package"),
        "git.workspace",
    ),
    ObjectiveCase(
        "routing-02",
        "tool_routing",
        "唯讀開啟公開網頁並擷取主要內容。",
        ("web.public_explore", "rest.public_api", "workspace.files"),
        "web.public_explore",
    ),
    ObjectiveCase(
        "routing-03",
        "tool_routing",
        "把多個已完成檔案封裝成可交付成品。",
        ("artifact.package", "data.analyze", "task.plan"),
        "artifact.package",
    ),
    ObjectiveCase(
        "routing-04",
        "tool_routing",
        "依照文字提示在本機產生圖片。",
        ("image.generate", "video.generate", "image.remove_background"),
        "image.generate",
    ),
    ObjectiveCase(
        "routing-05",
        "tool_routing",
        "在無 shell 與檔案權限下計算受限數學表達式。",
        ("python.safe_expression", "runtime.skill", "data.analyze"),
        "python.safe_expression",
    ),
    ObjectiveCase(
        "software-01",
        "software_engineering",
        "重試函式以遞迴方式無限重試。最直接的可靠性修正是什麼？",
        ("bounded_retry", "more_logging", "larger_timeout"),
        "bounded_retry",
    ),
    ObjectiveCase(
        "software-02",
        "software_engineering",
        "SQLite 正在寫入時需要一致備份，應使用哪個方案？",
        ("sqlite_backup", "copy_database_file", "zip_live_directory"),
        "sqlite_backup",
    ),
    ObjectiveCase(
        "software-03",
        "software_engineering",
        "兩個高階模組互相 import 形成循環；應優先採取哪個架構修正？",
        ("dependency_inversion", "global_state", "duplicate_code"),
        "dependency_inversion",
    ),
    ObjectiveCase(
        "software-04",
        "software_engineering",
        "任務先執行副作用，之後才檢查 idempotency key。正確順序是什麼？",
        ("check_idempotency_first", "keep_current_order", "disable_idempotency"),
        "check_idempotency_first",
    ),
    ObjectiveCase(
        "software-05",
        "software_engineering",
        "變更了持久化資料 schema，單元測試通過但沒有 migration。還缺少什麼？",
        ("migration_required", "prompt_change", "css_snapshot"),
        "migration_required",
    ),
)

OBJECTIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["id", "answer"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}

BENCHMARK_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "suite": BenchmarkSuite.MMLU.value,
        "name": "MMLU",
        "measures": "57 領域的多任務知識與問題解決",
        "source": "https://arxiv.org/abs/2009.03300",
    },
    {
        "suite": BenchmarkSuite.GSM8K.value,
        "name": "GSM8K",
        "measures": "多步驟小學數學文字題推理",
        "source": "https://arxiv.org/abs/2110.14168",
    },
    {
        "suite": BenchmarkSuite.FRONTIER_SCIENCE.value,
        "name": "FrontierScience",
        "measures": "物理、化學與生物的專家級科學推理",
        "source": "https://openai.com/index/frontierscience/",
    },
    {
        "suite": BenchmarkSuite.REAL_TASKS.value,
        "name": "Real Tasks",
        "measures": "20–50 個固定、可重播的真實課題與結構化量表",
        "source": None,
    },
    {
        "suite": BenchmarkSuite.ECK_P3_OBJECTIVE.value,
        "name": "ECK P3 Objective",
        "measures": "本機固定診斷：推理、證據判斷、工具路由與軟體工程",
        "source": None,
    },
)


class EvaluationService:
    def __init__(
        self,
        store: SQLiteStore,
        events: EventBus,
        brain: BrainProvider,
        resources: SystemResourceMonitor,
    ) -> None:
        self.store = store
        self.events = events
        self.brain = brain
        self.resources = resources

    async def record(self, create: BenchmarkRunCreate) -> BenchmarkRunRecord:
        if create.suite is BenchmarkSuite.REAL_TASKS and not 20 <= create.sample_count <= 50:
            raise ValueError("Real-task evaluations require 20 to 50 tasks.")
        if create.evaluator.casefold() in {"self", "same-model", "same_model"}:
            raise ValueError("A model cannot be the sole judge of its own growth claim.")
        record = self.store.add_benchmark_run(create)
        await self.events.publish(
            "BenchmarkRecorded",
            record.run_id,
            {
                "suite": record.suite.value,
                "model": record.model,
                "score": record.score,
                "sample_count": record.sample_count,
                "evaluator": record.evaluator,
            },
        )
        return record

    async def run_objective(
        self,
        request: ObjectiveEvaluationRequest,
    ) -> dict[str, Any]:
        health = await self.brain.health()
        if not health.available or not health.model:
            raise RuntimeError(health.detail or "The configured local brain is unavailable.")
        started = time.perf_counter()
        repetitions: list[dict[str, str]] = []
        for _ in range(request.repetitions):
            if health.provider == "mock":
                answers = {case.case_id: case.expected for case in OBJECTIVE_CASES}
            else:
                response = await self.brain.chat(
                    self._objective_messages(),
                    format_schema=OBJECTIVE_SCHEMA,
                    options={
                        "temperature": 0,
                        "think": False,
                        "num_predict": 1800,
                        "_priority": 5,
                    },
                )
                answers = self._parse_answers(response.content)
            repetitions.append(answers)

        case_results = []
        category_totals: dict[str, list[int]] = {}
        correct_total = 0
        stable_total = 0
        for case in OBJECTIVE_CASES:
            observed = [answers.get(case.case_id, "") for answers in repetitions]
            correctness = [answer == case.expected for answer in observed]
            correct_count = sum(correctness)
            correct_total += correct_count
            category_totals.setdefault(case.category, []).extend(int(item) for item in correctness)
            stable = bool(observed[0]) and len(set(observed)) == 1
            stable_total += int(stable)
            case_results.append(
                {
                    "id": case.case_id,
                    "category": case.category,
                    "correct_repetitions": correct_count,
                    "repetitions": request.repetitions,
                    "stable": stable,
                    "observed": observed,
                }
            )

        total_attempts = len(OBJECTIVE_CASES) * request.repetitions
        score = correct_total / total_attempts
        category_scores = {
            category: sum(values) / len(values)
            for category, values in sorted(category_totals.items())
        }
        reproducibility_rate = stable_total / len(OBJECTIVE_CASES)
        suite_hash = self._suite_hash()
        protocol = {
            "schema_version": "eck.p3.objective.v1",
            "scope": "public_local_diagnostic",
            "suite_hash": suite_hash,
            "repetitions": request.repetitions,
            "category_scores": category_scores,
            "reproducibility_rate": reproducibility_rate,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "case_results": case_results,
            "resource_snapshot": self.resources.quick_snapshot(),
            "growth_claim_allowed": False,
            "claim_limit": (
                "此公開診斷只建立本機能力基線；必須另有保留真實任務與回歸測試，"
                "才能宣稱能力提升。"
            ),
        }
        record = await self.record(
            BenchmarkRunCreate(
                suite=BenchmarkSuite.ECK_P3_OBJECTIVE,
                benchmark_version=f"p3-public-v1-{suite_hash[:12]}",
                model=health.model,
                model_artifact_hash=health.artifact_hash,
                evaluator="deterministic-exact-match",
                score=score,
                sample_count=len(OBJECTIVE_CASES),
                protocol=protocol,
                notes=(
                    "Public local diagnostic. It is not a hidden benchmark and cannot prove AGI, "
                    "top-one-percent engineering ability, or general intelligence growth."
                ),
            )
        )
        await self.events.publish(
            "ObjectiveEvaluationCompleted",
            record.run_id,
            {
                "score": score,
                "reproducibility_rate": reproducibility_rate,
                "suite_hash": suite_hash,
            },
        )
        return {
            "run": record.model_dump(mode="json"),
            "comparison": self.compare(BenchmarkSuite.ECK_P3_OBJECTIVE),
            "growth_audit": self.growth_audit(),
        }

    def compare(self, suite: BenchmarkSuite) -> dict[str, Any]:
        runs = [
            run
            for run in self.store.list_benchmark_runs(limit=1000)
            if run.suite is suite
        ]
        latest = runs[0] if runs else None
        previous = runs[1] if len(runs) > 1 else None
        comparable = bool(
            latest
            and previous
            and latest.benchmark_version == previous.benchmark_version
            and latest.model == previous.model
            and latest.model_artifact_hash
            and latest.model_artifact_hash == previous.model_artifact_hash
        )
        if latest is None:
            status = "no_baseline"
        elif previous is None:
            status = "baseline_created"
        elif not comparable:
            status = "conditions_changed"
        elif latest.score > previous.score:
            status = "diagnostic_improved"
        elif latest.score < previous.score:
            status = "diagnostic_regressed"
        else:
            status = "diagnostic_unchanged"
        return {
            "suite": suite.value,
            "status": status,
            "comparable": comparable,
            "latest": latest.model_dump(mode="json") if latest else None,
            "previous": previous.model_dump(mode="json") if previous else None,
            "delta": latest.score - previous.score if comparable and latest and previous else None,
            "claim_allowed": False,
        }

    def growth_audit(self) -> dict[str, Any]:
        cutoff = utc_now() - timedelta(hours=24)
        experiences = self.store.list_experiences(limit=10000)
        admitted = [item for item in experiences if item.admitted and item.created_at >= cutoff]
        research = [
            item
            for item in admitted
            if item.capability in {"web.critical_research", "academic.research"}
        ]
        memory_skills = self.store.list_skills(limit=10000)
        runtime_skills = self.store.list_runtime_skills(limit=10000)
        generated = [item for item in runtime_skills if item.source == "eck-generated"]
        generated_24h = [item for item in generated if item.created_at >= cutoff]
        active_generated_24h = [
            item for item in generated_24h if item.status is RuntimeSkillStatus.ACTIVE
        ]
        active_generated = [
            item for item in generated if item.status is RuntimeSkillStatus.ACTIVE
        ]
        draft_generated = [
            item for item in generated if item.status is RuntimeSkillStatus.DRAFT
        ]
        failed_generated = [
            item for item in generated if item.status is RuntimeSkillStatus.FAILED
        ]
        new_memory_skills = [item for item in memory_skills if item.created_at >= cutoff]
        if len(research) >= 10 and not active_generated_24h:
            status = "research_without_executable_skill_growth"
            message = (
                "最近 24 小時有大量已准入研究，但沒有新的 ECK 生成技能通過測試並啟用；"
                "不得宣稱可執行能力持續增強。"
            )
        elif active_generated_24h:
            status = "verified_executable_skill_growth"
            message = "最近 24 小時有新生成技能通過隔離測試並啟用。"
        elif admitted:
            status = "verified_learning_without_new_executable_skill"
            message = "最近 24 小時有已驗證學習，但沒有新的可執行技能。"
        else:
            status = "no_verified_learning_activity"
            message = "最近 24 小時沒有已驗證學習成果。"
        conversion_rate = (
            len(active_generated_24h) / len(research) if research else None
        )
        return {
            "window_hours": 24,
            "status": status,
            "message": message,
            "admitted_experiences": len(admitted),
            "research_admissions": len(research),
            "new_memory_skills": len(new_memory_skills),
            "generated_skill_candidates": len(generated_24h),
            "activated_generated_skills": len(active_generated_24h),
            "research_to_active_skill_rate": conversion_rate,
            "lifetime_generated_skills": len(generated),
            "lifetime_active_generated_skills": len(active_generated),
            "lifetime_draft_generated_skills": len(draft_generated),
            "lifetime_failed_generated_skills": len(failed_generated),
            "definitions": {
                "experience": "通過 Success Contract 的單次任務結果。",
                "memory_skill": "由重複成功歸納的可重用程序記憶。",
                "generated_skill": "ECK 寫出原始碼並經隔離測試的可執行技能。",
                "growth": "同條件固定評測改善，且沒有核心、安全或資源回歸。",
            },
        }

    def dashboard(self) -> dict[str, Any]:
        runs = self.store.list_benchmark_runs(limit=1000)
        items = []
        for definition in BENCHMARK_CATALOG:
            matching = [run for run in runs if run.suite.value == definition["suite"]]
            latest = matching[0] if matching else None
            previous = matching[1] if len(matching) > 1 else None
            items.append(
                {
                    **definition,
                    "latest": latest.model_dump(mode="json") if latest else None,
                    "delta": latest.score - previous.score if latest and previous else None,
                    "run_count": len(matching),
                }
            )
        objective_runs = [
            run for run in runs if run.suite is BenchmarkSuite.ECK_P3_OBJECTIVE
        ]
        return {
            "items": items,
            "objective": {
                "suite_version": f"p3-public-v1-{self._suite_hash()[:12]}",
                "case_count": len(OBJECTIVE_CASES),
                "comparison": self.compare(BenchmarkSuite.ECK_P3_OBJECTIVE),
                "history": [
                    self._objective_run_summary(run) for run in objective_runs[:12]
                ],
            },
            "growth_audit": self.growth_audit(),
            "claim_policy": (
                "只宣稱在固定版本、模型雜湊、保留測試集與獨立裁判下的可重現進步；"
                "公開診斷與有限基準不能證明已超越全人類。"
            ),
        }

    @staticmethod
    def _objective_messages() -> list[dict[str, str]]:
        cases = [
            {
                "id": case.case_id,
                "question": case.prompt,
                "allowed_answers": list(case.allowed_answers),
            }
            for case in OBJECTIVE_CASES
        ]
        return [
            {
                "role": "system",
                "content": (
                    "你正在接受固定、確定性評分的本機能力診斷。不得解釋，"
                    "每題只能從 allowed_answers 選一個完全相同的字串。"
                    "輸出必須是符合 schema 的 JSON，且每個 id 恰好出現一次。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"cases": cases}, ensure_ascii=False),
            },
        ]

    @classmethod
    def _parse_answers(cls, content: str) -> dict[str, str]:
        text = content.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), list):
            return {}
        known_ids = {case.case_id for case in OBJECTIVE_CASES}
        answers: dict[str, str] = {}
        for item in payload["answers"]:
            if not isinstance(item, dict):
                continue
            case_id = str(item.get("id", "")).strip()
            if case_id not in known_ids or case_id in answers:
                continue
            answers[case_id] = cls._normalize_answer(item.get("answer", ""))
        return answers

    @staticmethod
    def _normalize_answer(value: object) -> str:
        return str(value).strip().casefold().strip("`'\".,:;!?。；：！？")

    @staticmethod
    def _suite_hash() -> str:
        payload = [
            {
                "id": case.case_id,
                "category": case.category,
                "prompt": case.prompt,
                "allowed_answers": case.allowed_answers,
                "expected": case.expected,
            }
            for case in OBJECTIVE_CASES
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _objective_run_summary(run: BenchmarkRunRecord) -> dict[str, Any]:
        protocol = run.protocol
        return {
            "run_id": run.run_id,
            "created_at": run.created_at.isoformat(),
            "model": run.model,
            "model_artifact_hash": run.model_artifact_hash,
            "score": run.score,
            "sample_count": run.sample_count,
            "benchmark_version": run.benchmark_version,
            "repetitions": protocol.get("repetitions"),
            "reproducibility_rate": protocol.get("reproducibility_rate"),
            "category_scores": protocol.get("category_scores", {}),
            "latency_seconds": protocol.get("latency_seconds"),
        }
