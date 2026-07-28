from __future__ import annotations

from dataclasses import dataclass

from eck.brain.base import BrainProvider
from eck.brain.mock import MockBrainProvider
from eck.brain.ollama import OllamaBrainProvider
from eck.capabilities.gridworld import GridWorldCapability
from eck.capabilities.registry import CapabilityRegistry
from eck.capabilities.safe_python import SafePythonExpressionCapability
from eck.config import Settings
from eck.events.bus import EventBus
from eck.kernel.runtime import LifeKernel
from eck.memory.experience import ExperienceEngine
from eck.policy.gate import PolicyGate
from eck.services.tasks import TaskService
from eck.storage.sqlite import SQLiteStore
from eck.verification.verifier import ContractVerifier


@dataclass(slots=True)
class Application:
    settings: Settings
    store: SQLiteStore
    events: EventBus
    registry: CapabilityRegistry
    brain: BrainProvider
    tasks: TaskService
    kernel: LifeKernel


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or Settings()
    settings.prepare_directories()
    assert settings.database_path is not None
    store = SQLiteStore(settings.database_path)
    store.initialize()
    events = EventBus(store)

    registry = CapabilityRegistry()
    registry.register(SafePythonExpressionCapability())
    registry.register(GridWorldCapability(store))

    if settings.brain_provider == "mock":
        brain: BrainProvider = MockBrainProvider()
    else:
        brain = OllamaBrainProvider(
            settings.ollama_base_url,
            settings.ollama_model,
            settings.ollama_timeout_seconds,
        )

    task_service = TaskService(
        store=store,
        events=events,
        registry=registry,
        policy=PolicyGate(settings),
        verifier=ContractVerifier(),
        experiences=ExperienceEngine(store),
    )
    kernel = LifeKernel(settings, store, events, task_service)
    return Application(
        settings=settings,
        store=store,
        events=events,
        registry=registry,
        brain=brain,
        tasks=task_service,
        kernel=kernel,
    )

