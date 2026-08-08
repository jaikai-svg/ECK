from __future__ import annotations

import asyncio

import pytest

from eck.brain.arbiter import InferenceArbiter
from eck.core.time import utc_now
from eck.domain.enums import (
    ComparisonOperator,
    EvidenceSource,
    RiskLevel,
    TaskStatus,
    VerificationStatus,
)
from eck.domain.models import (
    ActionProposal,
    CapabilityResult,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
)


def _task_create(*, max_attempts: int = 3) -> TaskCreate:
    return TaskCreate(
        goal="Verify reliable task execution.",
        success_contract=SuccessContract(
            goal="Verify reliable task execution.",
            checks=(
                VerificationCheck(
                    name="completed",
                    path="metrics.all_passed",
                    operator=ComparisonOperator.EQ,
                    expected=True,
                ),
            ),
            required_evidence=(EvidenceSource.UNIT_TEST,),
            max_attempts=max_attempts,
        ),
        action=ActionProposal(
            capability="python.safe_expression",
            operation="evaluate",
            payload={
                "expression": "x + 1",
                "cases": [{"input": 1, "expected": 2}],
            },
            declared_risk=RiskLevel.LOW,
            reversible=True,
        ),
    )


@pytest.mark.asyncio
async def test_duplicate_active_task_is_suppressed(application) -> None:
    first = await application.tasks.submit(_task_create())
    duplicate = await application.tasks.submit(_task_create())

    assert duplicate.task_id == first.task_id
    assert application.store.count_tasks((TaskStatus.QUEUED,)) == 1
    assert "TaskDuplicateSuppressed" in {
        event.event_type for event in application.store.list_events(limit=20)
    }


@pytest.mark.asyncio
async def test_timeout_retries_with_backoff(application) -> None:
    task = await application.tasks.submit(_task_create(max_attempts=3))
    application.store.update_task(task.task_id, status=TaskStatus.RUNNING, attempts=1)

    retried = await application.tasks.handle_execution_timeout(task.task_id, 30)

    assert retried.status is TaskStatus.QUEUED
    assert retried.attempts == 1
    assert retried.next_attempt_at is not None
    assert application.store.list_ready_tasks() == []


@pytest.mark.asyncio
async def test_timeout_exhaustion_enters_dead_letter_state(application) -> None:
    task = await application.tasks.submit(_task_create(max_attempts=1))
    application.store.update_task(task.task_id, status=TaskStatus.RUNNING, attempts=1)

    blocked = await application.tasks.handle_execution_timeout(task.task_id, 30)

    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.last_error == "Execution exceeded 30.0 seconds."
    assert "TaskDeadLettered" in {
        event.event_type for event in application.store.list_events(limit=20)
    }


@pytest.mark.asyncio
async def test_video_quality_rejection_is_retryable(application) -> None:
    task = await application.tasks.submit(_task_create(max_attempts=3))
    task = application.store.update_task(task.task_id, attempts=1)
    now = utc_now()
    result = CapabilityResult(
        action_id=task.action.action_id,
        capability="video.generate",
        success=False,
        output={
            "error": (
                "Generated frames collapsed into a near-featureless image; "
                "artifact rejected."
            )
        },
        started_at=now,
        finished_at=now,
    )

    assert application.tasks._should_retry(
        task,
        result,
        VerificationStatus.VERIFIED_FAILURE,
    )


@pytest.mark.asyncio
async def test_inference_arbiter_prioritizes_queued_work() -> None:
    arbiter = InferenceArbiter()
    entered: list[str] = []
    release_first = asyncio.Event()

    async def run(name: str, priority: int, *, hold: bool = False) -> None:
        async with arbiter.slot(priority):
            entered.append(name)
            if hold:
                await release_first.wait()

    first = asyncio.create_task(run("first", 20, hold=True))
    await asyncio.sleep(0)
    low = asyncio.create_task(run("low", 100))
    high = asyncio.create_task(run("high", 0))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first, low, high)

    assert entered == ["first", "high", "low"]
