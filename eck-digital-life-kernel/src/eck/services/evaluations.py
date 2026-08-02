from __future__ import annotations

from typing import Any

from eck.domain.enums import BenchmarkSuite
from eck.domain.models import BenchmarkRunCreate, BenchmarkRunRecord
from eck.events.bus import EventBus
from eck.storage.sqlite import SQLiteStore

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
)


class EvaluationService:
    def __init__(self, store: SQLiteStore, events: EventBus) -> None:
        self.store = store
        self.events = events

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
                    "delta": (
                        latest.score - previous.score if latest and previous else None
                    ),
                    "run_count": len(matching),
                }
            )
        return {
            "items": items,
            "claim_policy": (
                "只宣稱在固定版本、保留測試集與獨立裁判下的可重現進步；"
                "有限基準不能證明已超越全人類。"
            ),
        }
