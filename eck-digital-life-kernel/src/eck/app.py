from __future__ import annotations

from dataclasses import dataclass

from eck.brain.arbiter import InferenceArbiter
from eck.brain.base import BrainProvider
from eck.brain.mock import MockBrainProvider
from eck.brain.ollama import OllamaBrainProvider
from eck.capabilities.academic_research import AcademicResearchCapability
from eck.capabilities.critical_research import CriticalResearchCapability
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
from eck.capabilities.self_inspect import SelfInspectCapability
from eck.capabilities.video_generation import VideoGenerationCapability
from eck.config import Settings
from eck.domain.models import EventRecord
from eck.events.bus import EventBus
from eck.kernel.runtime import LifeKernel
from eck.memory.experience import ExperienceEngine
from eck.policy.autonomy import AutonomyGate
from eck.policy.gate import PolicyGate
from eck.research.discovery import (
    BingNewsRSSDiscoveryClient,
    FallbackDiscoveryClient,
    GDELTDiscoveryClient,
)
from eck.runtime.resources import SystemResourceMonitor
from eck.runtime.worker import DockerSkillWorker
from eck.services.autonomous_learning import AutonomousLearningService
from eck.services.challenges import ChallengeService
from eck.services.community_sources import CommunitySourceCatalog
from eck.services.core_evolution import CoreEvolutionLabService
from eck.services.evaluations import EvaluationService
from eck.services.evolution import EvolutionAuditService
from eck.services.identity import IdentityService
from eck.services.mission_executor import DurableMissionExecutor
from eck.services.missions import MissionService
from eck.services.portability import CognitiveBundleService
from eck.services.project_lab import AutonomousProjectLabService
from eck.services.research_skill_bridge import ResearchSkillBridgeService
from eck.services.self_model import RepositorySelfModelService
from eck.services.skill_forge import SkillForgeService
from eck.services.skill_graph import SkillKnowledgeGraphService
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
    coder_brain: BrainProvider
    supervisor_brain: BrainProvider
    worker: DockerSkillWorker
    forge: SkillForgeService
    identity_service: IdentityService
    self_model: RepositorySelfModelService
    skill_bridge: ResearchSkillBridgeService
    core_lab: CoreEvolutionLabService
    project_lab: AutonomousProjectLabService
    tasks: TaskService
    supervisor: SupervisorService
    autonomous_learning: AutonomousLearningService
    community_sources: CommunitySourceCatalog
    challenges: ChallengeService
    missions: MissionService
    mission_executor: DurableMissionExecutor
    versions: VersionService
    evaluations: EvaluationService
    evolution: EvolutionAuditService
    portability: CognitiveBundleService
    skill_graph: SkillKnowledgeGraphService
    autonomy: AutonomyGate
    image_generation: ImageGenerationCapability
    image_background_removal: ImageBackgroundRemovalCapability
    video_generation: VideoGenerationCapability
    resources: SystemResourceMonitor
    kernel: LifeKernel


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or Settings()
    settings.prepare_directories()
    assert settings.database_path is not None
    store = SQLiteStore(settings.database_path)
    store.initialize()
    events = EventBus(store)
    resources = SystemResourceMonitor(settings)
    identity_service = IdentityService(settings)
    self_model = RepositorySelfModelService(settings)
    if settings.repository_self_model_enabled and settings.environment != "test":
        self_model.ensure()

    if settings.brain_provider == "mock":
        brain: BrainProvider = MockBrainProvider()
        coder_brain: BrainProvider = brain
        supervisor_brain: BrainProvider = MockBrainProvider()
    else:
        inference_arbiter = InferenceArbiter()
        brain = OllamaBrainProvider(
            settings.ollama_base_url,
            settings.ollama_model,
            settings.ollama_timeout_seconds,
            arbiter=inference_arbiter,
            default_priority=20,
            health_cache_seconds=settings.brain_health_cache_seconds,
        )
        supervisor_brain = OllamaBrainProvider(
            settings.ollama_base_url,
            settings.supervisor_model or settings.ollama_model,
            settings.ollama_timeout_seconds,
            arbiter=inference_arbiter,
            default_priority=100,
            health_cache_seconds=settings.brain_health_cache_seconds,
        )
        coder_brain = OllamaBrainProvider(
            settings.ollama_base_url,
            settings.coder_model or settings.ollama_model,
            settings.coder_timeout_seconds,
            arbiter=inference_arbiter,
            default_priority=40,
            health_cache_seconds=settings.brain_health_cache_seconds,
        )

    versions = VersionService(store, events)
    worker = DockerSkillWorker(settings)
    forge = SkillForgeService(settings, store, events, coder_brain, worker, versions)

    registry = CapabilityRegistry()
    registry.register(SafePythonExpressionCapability())
    registry.register(GridWorldCapability(store))
    registry.register(WorkspaceCapability(settings.workspace_dir))
    public_web = PublicWebCapability(settings)
    registry.register(public_web)
    registry.register(PublicRestCapability(settings))
    registry.register(DataAnalysisCapability())
    registry.register(ArtifactPackageCapability(settings.workspace_dir))
    registry.register(GitWorkspaceCapability(settings.workspace_dir))
    registry.register(TaskPlanningCapability(brain))
    registry.register(RuntimeSkillCapability(settings, store, worker))
    registry.register(SelfInspectCapability(self_model))
    image_generation = ImageGenerationCapability(settings, brain)
    registry.register(image_generation)
    image_background_removal = ImageBackgroundRemovalCapability(settings)
    registry.register(image_background_removal)
    video_generation = VideoGenerationCapability(settings, image_generation)
    registry.register(video_generation)
    skill_graph = SkillKnowledgeGraphService(
        store,
        capability_provider=lambda: (video_generation.skill_graph_snapshot(),),
    )
    registry.register(
        AcademicResearchCapability(
            brain,
            timeout_seconds=settings.academic_research_timeout_seconds,
            max_sources=settings.academic_research_max_sources,
        )
    )
    if settings.critical_research_enabled:
        registry.register(
            CriticalResearchCapability(
                settings,
                brain,
                store,
                public_web,
                FallbackDiscoveryClient(
                    BingNewsRSSDiscoveryClient(
                        timeout_seconds=settings.critical_research_timeout_seconds,
                        base_url=settings.critical_research_bing_rss_url,
                    ),
                    GDELTDiscoveryClient(
                        timeout_seconds=settings.critical_research_timeout_seconds,
                        base_url=settings.critical_research_gdelt_base_url,
                    ),
                ),
            )
        )

    task_service = TaskService(
        settings=settings,
        store=store,
        events=events,
        registry=registry,
        policy=PolicyGate(settings),
        verifier=ContractVerifier(),
        experiences=ExperienceEngine(store),
    )
    challenge_service = ChallengeService(store, events, brain)
    evaluation_service = EvaluationService(store, events, brain, resources)
    skill_bridge = ResearchSkillBridgeService(
        settings,
        store,
        events,
        coder_brain,
        forge,
        self_model,
    )
    core_lab = CoreEvolutionLabService(
        settings,
        events,
        coder_brain,
        self_model,
    )
    project_lab = AutonomousProjectLabService(
        settings,
        store,
        events,
        coder_brain,
        worker,
    )
    mission_service = MissionService(
        store,
        events,
        versions,
        task_service,
        registry,
    )
    mission_executor = DurableMissionExecutor(
        settings,
        store,
        events,
        coder_brain,
        project_lab,
        mission_service,
    )
    evolution_service = EvolutionAuditService(
        settings,
        store,
        worker,
        self_model,
        skill_bridge,
        core_lab,
        project_lab,
    )
    portability_service = CognitiveBundleService(
        settings,
        store,
        events,
        registry,
        versions,
    )
    autonomy_gate = AutonomyGate()
    community_sources = CommunitySourceCatalog(settings.community_source_catalog_path)
    supervisor_service = SupervisorService(
        settings,
        store,
        events,
        supervisor_brain,
        task_service,
        forge,
        registry,
    )
    autonomous_learning = AutonomousLearningService(
        settings,
        store,
        events,
        task_service,
        community_sources,
        self_model,
    )
    async def observe_verified_skill(_: EventRecord) -> None:
        await versions.observe_verified_skills()

    async def invalidate_skill_graph(_: EventRecord) -> None:
        skill_graph.invalidate()

    events.subscribe("SkillUpdated", observe_verified_skill)
    events.subscribe("SkillUpdated", invalidate_skill_graph)
    events.subscribe("TaskVerified", invalidate_skill_graph)
    events.subscribe("RuntimeSkillActivated", invalidate_skill_graph)
    events.subscribe("TaskVerified", mission_service.handle_task_verified)
    events.subscribe("MissionCreated", mission_executor.handle_mission_created)
    events.subscribe("MissionPlanUpdated", mission_executor.handle_plan_updated)
    kernel = LifeKernel(
        settings,
        store,
        events,
        task_service,
        supervisor_service,
        autonomous_learning,
        skill_bridge,
        project_lab,
        mission_executor,
        resources,
    )
    return Application(
        settings=settings,
        store=store,
        events=events,
        registry=registry,
        brain=brain,
        coder_brain=coder_brain,
        supervisor_brain=supervisor_brain,
        worker=worker,
        forge=forge,
        identity_service=identity_service,
        self_model=self_model,
        skill_bridge=skill_bridge,
        core_lab=core_lab,
        project_lab=project_lab,
        tasks=task_service,
        supervisor=supervisor_service,
        autonomous_learning=autonomous_learning,
        community_sources=community_sources,
        challenges=challenge_service,
        missions=mission_service,
        mission_executor=mission_executor,
        versions=versions,
        evaluations=evaluation_service,
        evolution=evolution_service,
        portability=portability_service,
        skill_graph=skill_graph,
        autonomy=autonomy_gate,
        image_generation=image_generation,
        image_background_removal=image_background_removal,
        video_generation=video_generation,
        resources=resources,
        kernel=kernel,
    )
