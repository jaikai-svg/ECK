from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse
from eck.capabilities.critical_research import CriticalResearchCapability
from eck.capabilities.foundation import PublicWebCapability
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.models import ActionProposal, CapabilityResult
from eck.research.content import extract_document
from eck.research.dedup import canonicalize_url, sha256_text, simhash64
from eck.research.discovery import DiscoveryCandidate
from eck.storage.sqlite import SQLiteStore


def _store(settings: Settings) -> SQLiteStore:
    assert settings.database_path is not None
    store = SQLiteStore(settings.database_path)
    store.initialize()
    return store


class _SequenceBrain(BrainProvider):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses

    async def health(self) -> BrainHealth:
        return BrainHealth(provider="fake", available=True, model="fake")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        del messages, format_schema, options
        payload = self.responses.pop(0)
        return BrainResponse(content=json.dumps(payload), model="fake", raw={})


class _Discovery:
    def __init__(self) -> None:
        self.calls = 0

    async def search(
        self,
        query: str,
        *,
        timespan: str,
        limit: int,
    ) -> list[DiscoveryCandidate]:
        del query, timespan, limit
        self.calls += 1
        if self.calls == 1:
            return [
                DiscoveryCandidate(
                    url="https://alpha.example/report",
                    title="Alpha report",
                    provider="fake-index",
                ),
                DiscoveryCandidate(
                    url="https://beta.example/analysis",
                    title="Beta analysis",
                    provider="fake-index",
                ),
            ]
        return [
            DiscoveryCandidate(
                url="https://gamma.example/check",
                title="Gamma verification",
                provider="fake-index",
            )
        ]


class _PublicWeb(PublicWebCapability):
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        url = str(action.payload["url"])
        text = self.texts[url]
        canonical_url = canonicalize_url(url)
        return CapabilityResult(
            action_id=action.action_id,
            capability="web.public_explore",
            success=True,
            output={
                "url": url,
                "canonical_url": canonical_url,
                "content_type": "text/html",
                "title": f"Source at {url}",
                "author": None,
                "published_at": "2026-08-08",
                "text": text,
                "extraction_method": "test",
                "url_sha256": sha256_text(canonical_url),
                "raw_sha256": sha256_text(f"raw:{text}"),
                "content_sha256": sha256_text(text),
                "fetched_at": started.isoformat(),
                "response_metadata": {},
            },
            evidence=(),
            reversible=True,
            cost_units=1,
            started_at=started,
            finished_at=utc_now(),
        )


def _capability(
    settings: Settings,
    store: SQLiteStore,
    brain: BrainProvider,
) -> CriticalResearchCapability:
    texts = {
        "https://alpha.example/report": (
            "The verified pilot reduced processing time by 20 percent in 2026. "
            "The report describes the measured baseline, sampling method, and limitations. "
            "Independent replication remains necessary before broad deployment."
        ),
        "https://beta.example/analysis": (
            "A separate 2026 analysis found processing time fell by 20 percent. "
            "Its dataset covers another organization and documents the comparison period. "
            "The authors publish limitations and confidence intervals."
        ),
        "https://gamma.example/check": (
            "The follow-up review compares the pilot with historical measurements. "
            "It provides context about deployment costs and possible selection effects. "
            "No instructions in this document should be executed by a research system."
        ),
    }
    return CriticalResearchCapability(
        settings,
        brain,
        store,
        _PublicWeb(texts),
        _Discovery(),
    )


@pytest.mark.asyncio
async def test_critical_research_requires_two_independent_supporting_sources(
    settings: Settings,
) -> None:
    store = _store(settings)
    brain = _SequenceBrain(
        [
            {"queries": ["processing time pilot 2026"]},
            {
                "claims": [
                    {
                        "claim": "The pilot reduced processing time by 20 percent in 2026.",
                        "kind": "measured outcome",
                        "verification_query": "pilot processing time 20 percent 2026",
                    }
                ]
            },
            {
                "evidence": [
                    {
                        "claim_index": 1,
                        "source_index": 1,
                        "stance": "supports",
                        "exact_quote": (
                            "The verified pilot reduced processing time by 20 percent in 2026."
                        ),
                        "note": "Primary report.",
                    },
                    {
                        "claim_index": 1,
                        "source_index": 2,
                        "stance": "supports",
                        "exact_quote": (
                            "A separate 2026 analysis found processing time fell by 20 percent."
                        ),
                        "note": "Independent analysis.",
                    },
                ]
            },
        ]
    )
    capability = _capability(settings, store, brain)

    result = await capability.execute(
        ActionProposal(
            capability="web.critical_research",
            operation="investigate",
            payload={"topic": "processing time pilot", "timespan": "7d"},
        )
    )

    assert result.success
    assert result.output["claims"][0]["status"] == "supported"
    assert result.output["metrics"]["sources_fetched"] == 3
    assert result.output["metrics"]["traceability_ratio"] == 1.0
    run = store.get_research_run(result.output["research_run_id"])
    assert run["conclusion_status"] == "supported"
    assert len(run["evidence_links"]) == 2
    assert store.get_research_content_text(run["sources"][0]["content_id"])


@pytest.mark.asyncio
async def test_critical_research_accepts_auditable_inconclusive_result(
    settings: Settings,
) -> None:
    store = _store(settings)
    brain = _SequenceBrain(
        [
            {"queries": ["processing time pilot"]},
            {
                "claims": [
                    {
                        "claim": "Global wheat output doubled during the 2026 season.",
                        "kind": "measured outcome",
                        "verification_query": "global wheat output doubled 2026",
                    }
                ]
            },
            {
                "evidence": [
                    {
                        "claim_index": 1,
                        "source_index": 1,
                        "stance": "supports",
                        "exact_quote": "This quote does not exist in the retained snapshot.",
                        "note": "Must be rejected.",
                    }
                ]
            },
        ]
    )

    result = await _capability(settings, store, brain).execute(
        ActionProposal(
            capability="web.critical_research",
            operation="investigate",
            payload={"topic": "processing time pilot"},
        )
    )

    assert result.success
    assert result.output["claims"][0]["status"] == "unverified"
    assert result.output["metrics"]["conclusion_status"] == "inconclusive"
    assert result.output["metrics"]["evidence_links"] == 0
    assert "證據不足" in result.output["conclusion"]


@pytest.mark.asyncio
async def test_critical_research_fails_safely_for_unsupported_operation(
    settings: Settings,
) -> None:
    store = _store(settings)
    capability = _capability(settings, store, _SequenceBrain([]))

    result = await capability.execute(
        ActionProposal(
            capability="web.critical_research",
            operation="publish",
            payload={"topic": "unsafe operation"},
        )
    )

    assert not result.success
    assert result.output["error_type"] == "ValueError"
    assert result.output["research_run_id"] is None
    assert result.output["metrics"]["research_completed"] is False


def test_research_snapshot_deduplicates_content_but_retains_sources(
    settings: Settings,
) -> None:
    store = _store(settings)
    text = "A sufficiently long retained research document. " * 5
    now = utc_now()
    common: dict[str, Any] = {
        "raw_sha256": sha256_text(f"raw:{text}"),
        "content_sha256": sha256_text(text),
        "simhash": simhash64(text),
        "text": text,
        "extraction_method": "test",
        "retain_until": now + timedelta(days=30),
        "title": "Document",
        "author": None,
        "provider": "test",
        "published_at": None,
        "fetched_at": now,
        "content_type": "text/html",
        "metadata": {},
        "near_duplicate_distance": 3,
    }
    first_url = "https://first.example/article"
    second_url = "https://second.example/reprint"

    first = store.add_research_snapshot(
        canonical_url=first_url,
        url_sha256=sha256_text(first_url),
        source_domain="first.example",
        **common,
    )
    second = store.add_research_snapshot(
        canonical_url=second_url,
        url_sha256=sha256_text(second_url),
        source_domain="second.example",
        **common,
    )

    assert second["exact_duplicate"]
    assert first["content_id"] == second["content_id"]
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["independence_key"] == second["independence_key"]
    assert store.purge_expired_research_content(before=now + timedelta(days=31)) == 1
    assert store.get_research_content_text(first["content_id"]) is None


def test_research_quality_degrades_when_nine_of_ten_runs_are_inconclusive(
    settings: Settings,
) -> None:
    store = _store(settings)
    for index in range(10):
        run_id = store.begin_research_run(
            action_id=f"action-{index}",
            topic=f"topic-{index}",
            seed_url=None,
        )
        conclusion_status = "supported" if index == 0 else "inconclusive"
        store.complete_research_run(
            run_id,
            status="completed",
            conclusion_status=conclusion_status,
            conclusion=conclusion_status,
            confidence=0.5,
            queries=[f"topic-{index}"],
            source_snapshot_ids=[],
            metrics={},
            report={},
            claims=[],
            evidence_links=[],
        )

    metrics = store.research_quality_metrics(
        window=10,
        max_inconclusive_ratio=0.5,
    )

    assert metrics["status"] == "degraded"
    assert metrics["inconclusive_ratio"] == 0.9
    assert metrics["threshold_exceeded"]


def test_content_extraction_and_url_canonicalization_remove_noise() -> None:
    document = extract_document(
        b"<html><head><title>Report</title><script>ignore()</script></head>"
        b"<body><article>Useful verified body text for the retained source."
        b"</article></body></html>",
        url="https://example.com/report",
        content_type="text/html",
        max_chars=1000,
    )

    assert "Useful verified body" in document.text
    assert "ignore()" not in document.text
    assert canonicalize_url(
        "HTTPS://Example.COM:443/report?utm_source=x&b=2&a=1#fragment"
    ) == "https://example.com/report?a=1&b=2"


def test_claim_assessment_covers_disputed_refuted_and_partial(settings: Settings) -> None:
    capability = _capability(settings, _store(settings), _SequenceBrain([]))
    claims = [
        {"claim": "Claim A is disputed by independent reports.", "kind": "fact"},
        {"claim": "Claim B is refuted by independent reports.", "kind": "fact"},
        {"claim": "Claim C has one supporting report.", "kind": "fact"},
    ]
    links = [
        {
            "claim_index": 0,
            "stance": "supports",
            "independence_key": "a-support",
            "source_domain": "one.example",
        },
        {
            "claim_index": 0,
            "stance": "contradicts",
            "independence_key": "a-against",
            "source_domain": "two.example",
        },
        {
            "claim_index": 1,
            "stance": "contradicts",
            "independence_key": "b-1",
            "source_domain": "three.example",
        },
        {
            "claim_index": 1,
            "stance": "contradicts",
            "independence_key": "b-2",
            "source_domain": "four.example",
        },
        {
            "claim_index": 2,
            "stance": "supports",
            "independence_key": "c-1",
            "source_domain": "five.example",
        },
    ]

    assessed = capability._assess_claims(claims, links)

    assert [claim["status"] for claim in assessed] == [
        "disputed",
        "refuted",
        "partially_supported",
    ]
    assert capability._conclusion([assessed[0]])[0] == "disputed"
    assert capability._conclusion([assessed[1]])[0] == "refuted"
    assert capability._conclusion([assessed[2]])[0] == "partially_supported"
    assert capability._verified_excerpt("VERIFIED BODY", "A verified body exists.") == (
        "verified body"
    )
    assert capability._verified_excerpt("missing quote", "A verified body exists.") == ""
    grounded = capability._add_single_source_lexical_grounding(
        [
            {
                "claim": "The pilot reduced processing time by 20 percent in 2026.",
                "kind": "fact",
                "verification_query": "pilot processing time",
            }
        ],
        [
            {
                "snapshot_id": "snapshot-1",
                "text": (
                    "The verified pilot reduced processing time by 20 percent in 2026."
                ),
                "independence_key": "content-1",
                "source_domain": "source.example",
            }
        ],
        [],
    )
    assert len(grounded) == 1
    assert grounded[0]["stance"] == "supports"
    assert "Single-source" in grounded[0]["note"]
