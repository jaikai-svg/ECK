from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from eck.domain.enums import RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    name: str
    description: str
    default_risk: RiskLevel
    deterministic: bool
    network_access: bool = False
    system_file_mutation: bool = False


class Capability(ABC):
    definition: CapabilityDefinition

    @abstractmethod
    async def execute(self, action: ActionProposal) -> CapabilityResult:
        raise NotImplementedError

