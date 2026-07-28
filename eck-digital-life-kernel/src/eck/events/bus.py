from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from eck.domain.models import EventRecord
from eck.storage.sqlite import SQLiteStore

EventHandler = Callable[[EventRecord], None | Awaitable[None]]


class EventBus:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(
        self,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> EventRecord:
        event = self.store.append_event(
            event_type,
            aggregate_id,
            payload,
            correlation_id=correlation_id,
        )
        handlers = [*self._handlers.get(event_type, ()), *self._handlers.get("*", ())]
        for handler in handlers:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        return event

    async def replay(
        self,
        handler: EventHandler,
        *,
        after_sequence: int = 0,
        page_size: int = 500,
    ) -> int:
        cursor = after_sequence
        while True:
            events = self.store.list_events(after_sequence=cursor, limit=page_size)
            if not events:
                return cursor
            for event in events:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
                cursor = event.sequence
            await asyncio.sleep(0)

