from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resource_pressure_event_is_rate_limited(application) -> None:
    kernel = application.kernel
    kernel._last_resource_pressure_event = -kernel.settings.resource_pressure_event_seconds
    before = application.store.count_events()
    pressure = {
        "level": "critical",
        "background_allowed": False,
        "reasons": ["available_memory_below_background_floor"],
    }

    await kernel._resource_pressure_throttled(pressure)
    await kernel._resource_pressure_throttled(pressure)

    assert application.store.count_events() == before + 1
    latest = application.store.list_events(limit=100)[-1]
    assert latest.event_type == "ResourcePressureThrottled"
    assert latest.payload["level"] == "critical"
