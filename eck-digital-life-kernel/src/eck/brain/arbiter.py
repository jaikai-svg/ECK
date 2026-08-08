from __future__ import annotations

import asyncio
import heapq
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import count


class InferenceArbiter:
    """Serialize local-model inference while honoring queued request priority."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._queue: list[tuple[int, int]] = []
        self._sequence = count()
        self._active = False

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        token = (priority, next(self._sequence))
        async with self._condition:
            heapq.heappush(self._queue, token)
            try:
                await self._condition.wait_for(
                    lambda: not self._active and self._queue[0] == token
                )
            except BaseException:
                self._remove(token)
                self._condition.notify_all()
                raise
            heapq.heappop(self._queue)
            self._active = True
        try:
            yield
        finally:
            async with self._condition:
                self._active = False
                self._condition.notify_all()

    def _remove(self, token: tuple[int, int]) -> None:
        try:
            self._queue.remove(token)
        except ValueError:
            return
        heapq.heapify(self._queue)
