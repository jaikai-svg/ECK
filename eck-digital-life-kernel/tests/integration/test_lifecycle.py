from __future__ import annotations

import pytest

from eck.app import build_application
from eck.domain.enums import KernelPhase


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

