from __future__ import annotations

import json

from eck.services.community_sources import CommunitySourceCatalog


def test_official_community_catalog_is_valid_and_matchable(application) -> None:
    status = application.community_sources.status()
    match = application.community_sources.match(
        "durable agent execution and checkpoint recovery"
    )

    assert status["available"]
    assert status["source_count"] == 8
    assert match is not None
    assert match["source_id"] == "langgraph"
    assert match["adoption_mode"] == "pattern-candidate"


def test_community_catalog_rejects_untrusted_or_credentialed_sources(tmp_path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "unsafe",
                        "name": "Unsafe",
                        "url": "https://user:secret@example.com/repo",
                        "owner": "unknown",
                        "trust_tier": "community",
                        "license": "unknown",
                        "topics": ["agent"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert CommunitySourceCatalog(path).list_sources() == []
