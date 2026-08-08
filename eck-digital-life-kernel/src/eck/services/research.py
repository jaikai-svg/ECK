from __future__ import annotations

from typing import TYPE_CHECKING

from eck.capabilities.academic_research import AcademicResearchCapability
from eck.domain.enums import (
    ApprovalStatus,
    ComparisonOperator,
    EvidenceSource,
    RiskLevel,
)
from eck.domain.models import (
    ActionProposal,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
)

if TYPE_CHECKING:
    from eck.app import Application


def build_research_task(
    topic: str,
    cycle: int = 1,
    total_cycles: int = 1,
    *,
    source: str = "human",
) -> TaskCreate:
    contract = SuccessContract(
        goal=f"Research {topic!r} with cited academic metadata, cycle {cycle}.",
        checks=(
            VerificationCheck(
                name="At least three scholarly sources found",
                path="metrics.sources_found",
                operator=ComparisonOperator.GTE,
                expected=3,
            ),
            VerificationCheck(
                name="At least three topic-relevant sources retained",
                path="metrics.relevant_sources",
                operator=ComparisonOperator.GTE,
                expected=3,
            ),
            VerificationCheck(
                name="Mean deterministic relevance is at least 0.6",
                path="metrics.mean_relevance",
                operator=ComparisonOperator.GTE,
                expected=0.6,
            ),
            VerificationCheck(
                name="At least three research questions generated",
                path="metrics.questions_generated",
                operator=ComparisonOperator.GTE,
                expected=3,
            ),
            VerificationCheck(
                name="A synthesis was produced",
                path="metrics.synthesis_present",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
            VerificationCheck(
                name="The synthesis cites at least two retained sources",
                path="metrics.synthesis_grounded",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
        required_evidence=(EvidenceSource.TOOL,),
        minimum_score=1.0,
        max_attempts=1,
        max_cost_units=100,
        require_reproducible=False,
        reversible_exploration_only=True,
    )
    label = "human-guided" if source == "human" else "supervisor-assigned"
    return TaskCreate(
        goal=f"建立「{topic}」學術研究課程，第 {cycle}/{total_cycles} 輪。",
        success_contract=contract,
        action=ActionProposal(
            capability="academic.research",
            operation="survey",
            payload={
                "topic": topic,
                "cycle": cycle,
                "total_cycles": total_cycles,
                "source": source,
            },
            declared_risk=RiskLevel.MEDIUM,
            reversible=True,
            estimated_cost_units=25,
        ),
        labels=(label, "academic-research", f"topic:{topic}"),
    )


def build_critical_research_task(
    topic: str,
    *,
    url: str | None = None,
    timespan: str | None = None,
    source: str = "human",
) -> TaskCreate:
    contract = SuccessContract(
        goal=f"Critically investigate current public information about {topic!r}.",
        checks=(
            VerificationCheck(
                name="The research process completed",
                path="metrics.research_completed",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
            VerificationCheck(
                name="Current-information discovery was attempted",
                path="metrics.discovery_attempted",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
            VerificationCheck(
                name="At least one public source was retained",
                path="metrics.sources_fetched",
                operator=ComparisonOperator.GTE,
                expected=1,
            ),
            VerificationCheck(
                name="At least one verifiable claim was extracted",
                path="metrics.claims_extracted",
                operator=ComparisonOperator.GTE,
                expected=1,
            ),
            VerificationCheck(
                name="Every fetched source has a traceable snapshot",
                path="metrics.traceability_ratio",
                operator=ComparisonOperator.EQ,
                expected=1.0,
            ),
            VerificationCheck(
                name="An auditable uncertainty report was produced",
                path="metrics.report_present",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
        required_evidence=(EvidenceSource.TOOL,),
        minimum_score=1.0,
        max_attempts=1,
        max_cost_units=100,
        require_reproducible=False,
        reversible_exploration_only=True,
    )
    label = "human-guided" if source == "human" else "supervisor-assigned"
    payload: dict[str, object] = {"topic": topic, "source": source}
    if url:
        payload["url"] = url
    if timespan:
        payload["timespan"] = timespan
    return TaskCreate(
        goal=f"批判查證「{topic}」的最新公開資訊與反例。",
        success_contract=contract,
        action=ActionProposal(
            capability="web.critical_research",
            operation="investigate",
            payload=payload,
            declared_risk=RiskLevel.MEDIUM,
            reversible=True,
            estimated_cost_units=35,
        ),
        labels=(label, "critical-current-research", f"topic:{topic}"),
    )


class ResearchCurriculumService:
    def __init__(self, application: Application) -> None:
        self.application = application

    async def submit(self, topic: str, cycles: int) -> dict[str, object]:
        topic = topic.strip()
        tasks = []
        await self.application.events.publish(
            "CurriculumStarted",
            self.application.settings.identity,
            {"topic": topic, "cycles": cycles, "source": "human"},
        )
        for cycle in range(1, cycles + 1):
            create = build_research_task(topic, cycle, cycles, source="human")
            task = await self.application.tasks.submit(create)
            approval = self.application.store.get_task_approval(task.task_id)
            if approval is not None:
                task = await self.application.tasks.decide_approval(
                    approval.approval_id,
                    ApprovalStatus.APPROVED,
                )
            tasks.append(task.model_dump(mode="json"))
        return {"topic": topic, "cycles": cycles, "tasks": tasks}

    async def submit_critical(
        self,
        topic: str,
        *,
        url: str | None = None,
        timespan: str | None = None,
    ) -> dict[str, object]:
        create = build_critical_research_task(
            topic.strip(),
            url=url,
            timespan=timespan,
            source="human",
        )
        task = await self.application.tasks.submit(create)
        approval = self.application.store.get_task_approval(task.task_id)
        if approval is not None:
            task = await self.application.tasks.decide_approval(
                approval.approval_id,
                ApprovalStatus.APPROVED,
            )
        return {"topic": topic.strip(), "task": task.model_dump(mode="json")}

    async def audit_relevance(self) -> dict[str, object]:
        capability = self.application.registry.get("academic.research")
        if not isinstance(capability, AcademicResearchCapability):
            raise RuntimeError("Academic research capability is unavailable.")

        revoked: list[str] = []
        tasks = self.application.store.list_tasks(limit=10000)
        for task in tasks:
            if task.status.value != "verified_success" or task.result is None:
                continue
            if task.action.capability != "academic.research":
                continue
            output = task.result.output
            topic = str(output.get("topic", task.action.payload.get("topic", "")))
            sources = output.get("sources", [])
            if not isinstance(sources, list):
                sources = []
            search_terms = output.get("search_terms", [topic])
            if not isinstance(search_terms, list):
                search_terms = [topic]
            relevant = capability.rank_relevant_sources(
                [str(item) for item in search_terms],
                [item for item in sources if isinstance(item, dict)],
            )
            mean_relevance = (
                sum(float(item["relevance_score"]) for item in relevant) / len(relevant)
                if relevant
                else 0.0
            )
            synthesis = str(output.get("synthesis", ""))
            grounded = capability.synthesis_is_grounded(synthesis, relevant)
            if len(relevant) >= 3 and mean_relevance >= 0.6 and grounded:
                continue
            if not grounded:
                reason = "The synthesis was generic or lacked citations to retained sources."
            else:
                reason = (
                    f"Only {len(relevant)} topic-relevant sources remained; "
                    f"mean relevance was {mean_relevance:.3f}."
                )
            if not self.application.store.revoke_task_learning(task.task_id, reason):
                continue
            fingerprint = str(output.get("skill_fingerprint", ""))
            if fingerprint:
                self.application.store.revoke_skill_success(fingerprint)
            revoked.append(task.task_id)
            await self.application.events.publish(
                "LearningAdmissionRevoked",
                task.task_id,
                {"capability": "academic.research", "reason": reason},
                correlation_id=task.task_id,
            )
        return {"audited": len(tasks), "revoked": revoked, "revoked_count": len(revoked)}
