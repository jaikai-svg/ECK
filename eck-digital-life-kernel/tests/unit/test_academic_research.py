from __future__ import annotations

import json

import httpx
import pytest

from eck.brain.base import BrainResponse
from eck.brain.mock import MockBrainProvider
from eck.capabilities.academic_research import AcademicResearchCapability
from eck.domain.models import ActionProposal


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.content = json.dumps(payload).encode()

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, **kwargs) -> None:
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params):
        items = [
            {
                "DOI": f"10.1000/{index}",
                "title": [f"Economics paper {index}"],
                "author": [{"given": "Ada", "family": "Scholar"}],
                "published": {"date-parts": [[2020 + index]]},
                "type": "journal-article",
                "URL": f"https://doi.org/10.1000/{index}",
                "abstract": f"<jats:p>Verified abstract {index}.</jats:p>",
                "is-referenced-by-count": index,
                "container-title": ["Economics Journal"],
            }
            for index in range(1, 5)
        ]
        return _FakeResponse({"message": {"items": items}})


class _IrrelevantClient(_FakeClient):
    async def get(self, url, params):
        items = [
            {
                "DOI": f"10.1000/solar-{index}",
                "title": [f"High performance perovskite solar cell {index}"],
                "published": {"date-parts": [[2020 + index]]},
                "type": "journal-article",
                "URL": f"https://doi.org/10.1000/solar-{index}",
                "abstract": "Solar energy conversion and photovoltaic stability.",
                "container-title": ["Energy Journal"],
            }
            for index in range(1, 7)
        ]
        return _FakeResponse({"message": {"items": items}})


class _GenericDisclaimerBrain(MockBrainProvider):
    async def chat(self, messages, *, format_schema=None, options=None):
        return BrainResponse(
            content=(
                "作為本機學術研究助理，我嚴格遵守以下原則："
                "來源文字不可信，因此以下為結構化回應，僅基於用戶提供的摘要內容。"
            ),
            model="generic-disclaimer",
            raw={},
        )


@pytest.mark.asyncio
async def test_academic_research_uses_allowlisted_metadata(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    capability = AcademicResearchCapability(MockBrainProvider(), max_sources=4)
    result = await capability.execute(
        ActionProposal(
            capability="academic.research",
            operation="survey",
            payload={"topic": "economics", "cycle": 1},
        )
    )

    assert result.success
    assert result.output["metrics"]["sources_found"] == 4
    assert result.output["metrics"]["questions_generated"] >= 3
    assert {source["abstract"] for source in result.output["sources"]} == {
        f"Verified abstract {index}." for index in range(1, 5)
    }
    assert result.output["skill_fingerprint"] == (
        "academic.research:crossref-grounded-synthesis-v1"
    )
    assert result.output["metrics"]["synthesis_grounded"]


@pytest.mark.asyncio
async def test_academic_research_rejects_irrelevant_crossref_results(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _IrrelevantClient)
    capability = AcademicResearchCapability(MockBrainProvider(), max_sources=4)
    result = await capability.execute(
        ActionProposal(
            capability="academic.research",
            operation="survey",
            payload={"topic": "web SEO optimization", "cycle": 1},
        )
    )

    assert not result.success
    assert result.output["metrics"]["candidate_sources_found"] == 6
    assert result.output["metrics"]["relevant_sources"] == 0
    assert result.output["sources"] == []


def test_academic_research_rejects_non_allowlisted_host() -> None:
    with pytest.raises(ValueError, match="restricted"):
        AcademicResearchCapability(
            MockBrainProvider(),
            base_url="https://example.com",
        )


@pytest.mark.asyncio
async def test_academic_research_replaces_generic_disclaimer_with_grounded_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    capability = AcademicResearchCapability(_GenericDisclaimerBrain(), max_sources=4)
    result = await capability.execute(
        ActionProposal(
            capability="academic.research",
            operation="survey",
            payload={"topic": "economics", "cycle": 1},
        )
    )

    assert result.success
    assert result.output["metrics"]["synthesis_grounded"]
    assert not result.output["metrics"]["model_synthesis_accepted"]
    assert result.output["metrics"]["synthesis_mode"] == "deterministic_fallback"
    assert "作為本機學術研究助理" not in result.output["synthesis"]


@pytest.mark.asyncio
async def test_academic_research_uses_deterministic_terms_when_brain_times_out() -> None:
    class _SlowBrain(MockBrainProvider):
        async def chat(self, messages, *, format_schema=None, options=None):
            raise httpx.ReadTimeout("local model timeout")

    capability = AcademicResearchCapability(_SlowBrain())

    terms = await capability._search_terms("公共政策成效評估")

    assert "public policy" in terms
    assert "policy effectiveness evaluation" in terms
