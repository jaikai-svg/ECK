from __future__ import annotations

from dataclasses import dataclass

from eck.brain.base import BrainProvider
from eck.brain.mock import MockBrainProvider
from eck.brain.ollama import OllamaBrainProvider
from eck.capabilities.academic_research import AcademicResearchCapability
from eck.capabilities.foundation import (
    ArtifactPackageCapability,
    DataAnalysisCapability,
    GitWorkspaceCapability,
    PublicRestCapability,
    PublicWebCapability,
    TaskPlanningCapability,
    WorkspaceCapability,
)
from eck.capabilities.gridworld import GridWorldCapability
from eck.capabilities.image_background import ImageBackgroundRemovalCapability
from eck.capabilities.image_generation import ImageGenerationCapability
from eck.capabilities.registry import CapabilityRegistry
from eck.capabilities.runtime_skill import RuntimeSkillCapability
from eck.capabilities.safe_python import SafePythonExpressionCapability
from eck.config import Settings
from eck.domain.models import EventRecord
from eck.events.bus import EventBus
from eck.kernel.runtime import LifeKernel
from eck.memory.experience import ExperienceEngine
from eck.policy.autonomy import AutonomyGate
from eck.policy.gate import PolicyGate
from eck.runtime.worker import DockerSkillWorker
from eck.services.challenges import ChallengeService
from eck.services.evaluations import EvaluationService
from eck.services.missions import MissionService
from eck.services.skill_forge import SkillForgeService
from eck.services.supervisor import SupervisorService
from eck.services.tasks import TaskService
from eck.services.versioning import VersionService
from eck.storage.sqlite import SQLiteStore
from eck.verification.verifier import ContractVerifier


@dataclass(slots=True)
class Application:
    settings: Settings
    store: SQLiteStore
    events: EventBus
    registry: CapabilityRegistry
    brain: BrainProvider
    supervisor_brain: BrainProvider
    worker: DockerSkillWorker
    forge: SkillForgeService
    tasks: TaskService
    supervisor: SupervisorService
    challenges: ChallengeService
    missions: MissionService
    versions: VersionService
    evaluations: EvaluationService
    autonomy: AutonomyGate
    image_generation: ImageGenerationCapability
    image_background_removal: ImageBackgroundRemovalCapability
    kernel: LifeKernel


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or Settings()
    settings.prepare_directories()
    assert settings.database_path is not None
    store = SQLiteStore(settings.database_path)
    store.initialize()
    events = EventBus(store)

    if settings.brain_provider == "mock":
        brain: BrainProvider = MockBrainProvider()
        supervisor_brain: BrainProvider = MockBrainProvider()
    else:
        brain = OllamaBrainProvider(
            settings.ollama_base_url,
            settings.ollama_model,
            settings.ollama_timeout_seconds,
        )
        supervisor_brain = OllamaBrainProvider(
            settings.ollama_base_url,
            settings.supervisor_model or settings.ollama_model,
            settings.ollama_timeout_seconds,
        )

    versions = VersionService(store, events)
    worker = DockerSkillWorker(settings)
    forge = SkillForgeService(settings, store, events, brain, worker, versions)

    registry = CapabilityRegistry()
    registry.register(SafePythonExpressionCapability())
    registry.register(GridWorldCapability(store))
    registry.register(WorkspaceCapability(settings.workspace_dir))
    registry.register(PublicWebCapability(settings))
    registry.register(PublicRestCapability(settings))
    registry.register(DataAnalysisCapability())
    registry.register(ArtifactPackageCapability(settings.workspace_dir))
    registry.register(GitWorkspaceCapability(settings.workspace_dir))
    registry.register(TaskPlanningCapability(brain))
    registry.register(RuntimeSkillCapability(settings, store, worker))
    image_generation = ImageGenerationCapability(settings, brain)
    registry.register(image_generation)
    image_background_removal = ImageBackgroundRemovalCapability(settings)
    registry.register(image_background_removal)
    registry.register(
        AcademicResearchCapability(
            brain,
            timeout_seconds=settings.academic_research_timeout_seconds,
            max_sources=settings.academic_research_max_sources,
        )
    )

    task_service = TaskService(
        store=store,
        events=events,
        registry=registry,
        policy=PolicyGate(settings),
        verifier=ContractVerifier(),
        experiences=ExperienceEngine(store),
    )
    challenge_service = ChallengeService(store, events, brain)
    mission_service = MissionService(
        store,
        events,
        versions,
        task_service,
        registry,
    )
    evaluation_service = EvaluationService(store, events)
    autonomy_gate = AutonomyGate()
    supervisor_service = SupervisorService(
        settings,
        store,
        events,
        supervisor_brain,
        task_service,
        forge,
        registry,
    )
    async def observe_verified_skill(_: EventRecord) -> None:
        await versions.observe_verified_skills()

    events.subscribe("SkillUpdated", observe_verified_skill)
    events.subscribe("TaskVerified", mission_service.handle_task_verified)
    kernel = LifeKernel(settings, store, events, task_service, supervisor_service)
    return Application(
        settings=settings,
        store=store,
        events=events,
        registry=registry,
        brain=brain,
        supervisor_brain=supervisor_brain,
        worker=worker,
        forge=forge,
        tasks=task_service,
        supervisor=supervisor_service,
        challenges=challenge_service,
        missions=mission_service,
        versions=versions,
        evaluations=evaluation_service,
        autonomy=autonomy_gate,
        image_generation=image_generation,
        image_background_removal=image_background_removal,
        kernel=kernel,
    )
