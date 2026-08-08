from __future__ import annotations


def test_skill_graph_marks_verified_active_skills_gold_and_traceable(application) -> None:
    store = application.store
    basis = {"evidence_ids": ["evidence-video-1"], "status": "verified_success"}
    for _ in range(2):
        store.upsert_skill_success(
            fingerprint="video.generate:cogvideox-2b:v1",
            name="CogVideoX-2B local text-to-video",
            capability="video.generate",
            procedure={"backend": "cogvideox", "offload": "sequential_cpu"},
            verification_basis=basis,
        )

    graph = application.skill_graph.build(force=True)
    skill = next(
        item
        for item in graph["items"]
        if item.get("fingerprint") == "video.generate:cogvideox-2b:v1"
    )

    assert skill["gold"] is True
    assert skill["status"] == "acquired"
    assert skill["path"] == ["AI", "影片生成"]
    assert any(source.get("source_type") == "repository" for source in skill["sources"])
    assert any(source.get("reference") == "evidence-video-1" for source in skill["sources"])
    assert graph["portable"] is True
    assert graph["stats"]["acquired_skills"] == 1

    matches = application.skill_graph.search("影片生成 CogVideo", limit=3)
    assert matches[0]["gold"] is True
    assert matches[0]["capability"] == "video.generate"


def test_skill_graph_includes_verified_capability_snapshots(application) -> None:
    application.skill_graph.capability_provider = lambda: (
        {
            "fingerprint": "video.generate:cogvideox-2b:v1",
            "title": "本機影片生成：CogVideoX-2B",
            "capability": "video.generate",
            "description": "Verified local video generation.",
            "acquired": True,
            "runtime_available": True,
            "procedure": {"backend": "cogvideox", "model": "zai-org/CogVideoX-2b"},
            "verification": {"verified": True},
        },
    )

    graph = application.skill_graph.build(force=True)
    capability = next(
        item for item in graph["items"] if item.get("type") == "capability"
    )

    assert capability["gold"] is True
    assert capability["path"] == ["AI", "影片生成"]
    assert graph["stats"]["acquired_skills"] == 1
    assert application.skill_graph.search("影片生成", limit=3)[0]["title"] == capability["title"]
