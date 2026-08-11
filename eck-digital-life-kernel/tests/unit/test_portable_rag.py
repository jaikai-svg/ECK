from __future__ import annotations

from typing import Any

import pytest

from eck.domain.enums import ComparisonOperator, RiskLevel, VerificationStatus
from eck.domain.models import ActionProposal, SuccessContract, TaskCreate, VerificationCheck
from eck.memory.rag import PortableRagService


class DeterministicRagRuntime:
    dimension = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        query_tokens = set(query.casefold().split())
        return [
            float(len(query_tokens & set(document.casefold().split())))
            for document in documents
        ]

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "models_verified": True,
            "embedding_model": "deterministic-test",
            "reranker_model": "deterministic-test",
        }

    def close(self) -> None:
        return None

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.casefold()
        if "利率" in normalized or "金融" in normalized:
            return [1.0, 0.0, 0.0, 0.0]
        if "dog" in normalized or "狗" in normalized:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]


def create_source_task(application, task_id: str) -> None:
    application.store.create_task(
        task_id,
        TaskCreate(
            goal=f"RAG source {task_id}",
            success_contract=SuccessContract(
                goal=f"RAG source {task_id}",
                checks=(
                    VerificationCheck(
                        name="recorded",
                        path="recorded",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                ),
            ),
            action=ActionProposal(
                capability="research.claim",
                operation="record",
                payload={},
            ),
        ),
        RiskLevel.LOW,
    )


@pytest.mark.asyncio
async def test_portable_rag_indexes_verified_memory_and_reranks(application) -> None:
    create_source_task(application, "task-finance")
    create_source_task(application, "task-dog")
    relevant = application.store.add_knowledge(
        task_id="task-finance",
        capability="research.finance",
        claim="金融 利率 上升會提高借貸成本",
        outcome=VerificationStatus.VERIFIED_SUCCESS,
        evidence_ids=("evidence-finance",),
        externally_grounded=True,
        reproducible=True,
        admitted=True,
    )
    application.store.add_knowledge(
        task_id="task-dog",
        capability="image.generate",
        claim="dog 狗 在公園玩球",
        outcome=VerificationStatus.VERIFIED_SUCCESS,
        evidence_ids=("evidence-dog",),
        externally_grounded=True,
        reproducible=True,
        admitted=True,
    )
    service = PortableRagService(
        application.settings,
        application.store,
        runtime=DeterministicRagRuntime(),
    )

    first = await service.retrieve("金融 利率")
    second = await service.retrieve("金融 利率")

    assert first["available"] is True
    assert first["indexed"] == 2
    assert first["coarse_candidates"] == 2
    assert first["items"][0]["document_id"] == f"knowledge:{relevant.knowledge_id}"
    assert first["items"][0]["source_uri"].startswith("eck://knowledge/")
    assert second["indexed"] == 0
    assert service.status()["integrity"]["valid"] is True


@pytest.mark.asyncio
async def test_portable_rag_never_indexes_unverified_claims(application) -> None:
    create_source_task(application, "task-unverified")
    application.store.add_knowledge(
        task_id="task-unverified",
        capability="research.claim",
        claim="沒有證據的說法",
        outcome=VerificationStatus.UNVERIFIABLE,
        evidence_ids=(),
        externally_grounded=False,
        reproducible=False,
        admitted=False,
    )
    service = PortableRagService(
        application.settings,
        application.store,
        runtime=DeterministicRagRuntime(),
    )

    result = await service.retrieve("沒有證據")

    assert result["items"] == []
    assert service.status()["indexed_documents"] == 0
