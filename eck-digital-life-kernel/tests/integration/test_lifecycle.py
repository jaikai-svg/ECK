from __future__ import annotations

import pytest

from eck.app import build_application
from eck.domain.enums import (
    ComparisonOperator,
    EvidenceSource,
    KernelPhase,
    RiskLevel,
    TaskStatus,
)
from eck.domain.models import (
    ActionProposal,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
)


@pytest.mark.asyncio
async def test_kernel_survives_reconstruction_with_same_identity(settings) -> None:
    first = build_application(settings)
    await first.kernel.start()
    await first.events.publish("ObservationCreated", "observation-test", {"value": 42})
    await first.kernel.stop(clean=False)
    first_count = first.store.count_events()

    second = build_application(settings)
    await second.kernel.start()
    status = second.kernel.status()
    assert status.phase is KernelPhase.RUNNING
    assert status.boot_count == 2
    assert second.store.count_events() > first_count
    event_types = [event.event_type for event in second.store.list_events(limit=100)]
    assert "ObservationCreated" in event_types
    assert "KernelRecovered" in event_types
    await second.kernel.stop(clean=True)


@pytest.mark.asyncio
async def test_sleep_cycle_verifies_event_chain(application) -> None:
    await application.kernel.start()
    await application.kernel.run_sleep_cycle()
    events = application.store.list_events(limit=100)
    types = [event.event_type for event in events]
    assert "SleepStarted" in types
    assert "MemoryConsolidated" in types
    assert "SleepFinished" in types
    await application.kernel.stop()


@pytest.mark.asyncio
async def test_kernel_requeues_interrupted_reversible_task(settings) -> None:
    first = build_application(settings)
    task = await first.tasks.submit(
        TaskCreate(
            goal="Verify interrupted task recovery.",
            success_contract=SuccessContract(
                goal="Verify interrupted task recovery.",
                checks=(
                    VerificationCheck(
                        name="completed",
                        path="metrics.all_passed",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                ),
                required_evidence=(EvidenceSource.UNIT_TEST,),
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
    )
    first.store.update_task(task.task_id, status=TaskStatus.RUNNING, attempts=1)

    second = build_application(settings.model_copy(update={"task_poll_seconds": 60}))
    await second.kernel.start()
    recovered = second.store.get_task(task.task_id)

    assert recovered.status is TaskStatus.QUEUED
    assert recovered.attempts == 1
    assert recovered.next_attempt_at is not None
    assert recovered.last_error == "kernel_restart"
    assert "TaskInterruptedRecovered" in {
        event.event_type for event in second.store.list_events(limit=100)
    }
    await second.kernel.stop()
