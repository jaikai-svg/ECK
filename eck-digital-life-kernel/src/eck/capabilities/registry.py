from __future__ import annotations

from eck.capabilities.base import Capability


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        name = capability.definition.name
        if name in self._items:
            raise ValueError(f"Capability already registered: {name}")
        self._items[name] = capability

    def get(self, name: str) -> Capability | None:
        return self._items.get(name)

    def list(self) -> list[dict[str, object]]:
        return [
            {
                "name": item.definition.name,
                "description": item.definition.description,
                "default_risk": item.definition.default_risk.value,
                "deterministic": item.definition.deterministic,
                "network_access": item.definition.network_access,
                "autonomous_safe": item.definition.autonomous_safe,
                "system_file_mutation": item.definition.system_file_mutation,
            }
            for item in sorted(self._items.values(), key=lambda item: item.definition.name)
        ]
