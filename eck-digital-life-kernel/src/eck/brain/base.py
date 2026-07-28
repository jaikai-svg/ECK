from __future__ import annotations

from abc import ABC, abstractmethod
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


class BrainProvider(ABC):
    @abstractmethod
    async def health(self) -> BrainHealth:
        raise NotImplementedError

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
    ) -> BrainResponse:
        raise NotImplementedError

