from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict


class BrainHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    available: bool
    model: str | None = None
    detail: str = ""


class BrainResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    raw: dict[str, Any]


@asynccontextmanager
async def _unrestricted_resource_slot() -> AsyncIterator[None]:
    yield


class BrainProvider(ABC):
    def resource_slot(self, priority: int) -> AbstractAsyncContextManager[None]:
        del priority
        return _unrestricted_resource_slot()

    @abstractmethod
    async def health(self) -> BrainHealth:
        raise NotImplementedError

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        raise NotImplementedError
