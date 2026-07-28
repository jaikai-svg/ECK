from __future__ import annotations

import pytest

from eck.events.bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_dispatch_and_replay(application) -> None:
    seen: list[str] = []
    replayed: list[int] = []

    async def asynchronous_handler(event) -> None:
        seen.append(f"async:{event.event_type}")

    def wildcard_handler(event) -> None:
        seen.append(f"wild:{event.event_type}")

    application.events.subscribe("Example", asynchronous_handler)
    application.events.subscribe("*", wildcard_handler)
    await application.events.publish("Example", "aggregate", {"value": 1})
    await application.events.publish("Other", "aggregate", {"value": 2})

    assert seen == ["async:Example", "wild:Example", "wild:Other"]

    replay_bus = EventBus(application.store)
    cursor = await replay_bus.replay(
        lambda event: replayed.append(event.sequence),
        page_size=1,
    )
    assert replayed == [1, 2]
    assert cursor == 2

