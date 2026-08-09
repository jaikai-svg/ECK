from __future__ import annotations

from fastapi.testclient import TestClient

from eck.api.main import create_api
from eck.app import build_application


def test_health_dashboard_and_acceptance(application) -> None:
    api = create_api(application=application)
    with TestClient(api) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Digital Life Kernel" in dashboard.text
        assert 'id="uptime"' in dashboard.text

        stylesheet = client.get("/static/styles.css")
        assert stylesheet.status_code == 200
        assert stylesheet.headers["content-type"] == "text/css; charset=utf-8"

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["event_chain"]["valid"]
        assert health.json()["learning_progress"]["stall_threshold_minutes"] == 30
        assert health.json()["critical_research"]["status"] == "insufficient_history"

        chat = client.post("/v1/chat", json={"message": "What have you learned?"})
        assert chat.status_code == 200
        assert chat.json()["model"] == "mock-deterministic"

        commands = client.get("/v1/chat/commands")
        assert commands.status_code == 200
        command_names = {item["command"] for item in commands.json()["items"]}
        assert {"/image", "/video", "/status", "/help"} <= command_names

        assert client.get("/v1/kernel/status").status_code == 200
        supervisor = client.get("/v1/supervisor/status")
        assert supervisor.status_code == 200
        assert supervisor.json()["enabled"] is False
        assert client.post("/v1/kernel/start").json()["phase"] == "running"
        assert client.post("/v1/kernel/pause").json()["phase"] == "paused"
        assert client.post("/v1/kernel/resume").json()["phase"] == "running"
        assert client.post("/v1/kernel/sleep").json()["accepted"]
        capabilities = client.get("/v1/capabilities").json()["items"]
        assert len(capabilities) == 16
        assert any(item["name"] == "core.self_inspect" for item in capabilities)
        image_status = client.get("/v1/image/status")
        assert image_status.status_code == 200
        assert image_status.json()["quality"]["steps"] == 36
        assert "background_removal" in image_status.json()
        community_sources = client.get("/v1/learning/community-sources")
        assert community_sources.status_code == 200
        assert community_sources.json()["source_count"] == 11
        theme = client.post("/v1/learning/themes", json={"title": "股票"})
        assert theme.status_code == 201
        theme_id = theme.json()["theme_id"]
        themes = client.get("/v1/learning/themes")
        assert themes.json()["items"][0]["title"] == "股票"
        assert themes.json()["theme_focus_percent"] == 30
        paused = client.patch(
            f"/v1/learning/themes/{theme_id}", json={"active": False}
        )
        assert paused.json()["active"] is False
        assert client.delete(f"/v1/learning/themes/{theme_id}").status_code == 204
        skill_tree = client.get("/v1/learning/skill-tree")
        assert skill_tree.status_code == 200
        assert skill_tree.json()["schema_version"] == "eck-skill-knowledge-graph.v1"
        assert skill_tree.json()["portable"] is True
        skill_search = client.get("/v1/learning/skill-tree/search?q=browser")
        assert skill_search.status_code == 200
        assert skill_search.json()["items"][0]["title"] == "browser.explore"
        evolution = client.get("/v1/evolution/status")
        assert evolution.status_code == 200
        assert evolution.json()["verified_now"]["skill_self_authoring"] is True
        assert evolution.json()["not_yet_verified"]["automatic_structural_core_activation"]
        soul = client.get("/v1/identity/soul")
        assert soul.status_code == 200
        assert soul.json()["integrity_valid"] is True
        self_model = client.post("/v1/self-model/refresh")
        assert self_model.status_code == 200
        assert self_model.json()["summary"]["python_modules"] > 0
        bridge = client.get("/v1/evolution/skill-bridge")
        assert bridge.status_code == 200
        assert bridge.json()["conversion_verified"] is False
        core_candidates = client.get("/v1/evolution/core-candidates")
        assert core_candidates.status_code == 200
        assert core_candidates.json()["status"]["live_core_mutation"] is False

        roadmap = client.get("/v1/roadmap")
        assert roadmap.status_code == 200
        assert roadmap.json()["classification"] == "long_term_target"
        assert "不是已證實的 AGI" in roadmap.json()["current_truth"]
        assert any(
            item["version"] == "P2" and item["state"] == "verified"
            for item in roadmap.json()["milestones"]
        )
        assert any(
            item["version"] == "P4" and item["state"] == "verified"
            for item in roadmap.json()["milestones"]
        )

        resources = client.get("/v1/system/resources")
        assert resources.status_code == 200
        assert resources.json()["project"]["measurement"] == (
            "logical_readable_file_size"
        )
        assert resources.json()["host"]["memory"]["total_bytes"] >= 0
        assert resources.json()["pressure"]["level"] in {
            "normal",
            "moderate",
            "high",
            "critical",
        }
        assert "video_generation" in resources.json()["workloads"]

        code = client.post("/v1/demos/safe-code")
        assert code.status_code == 200
        assert code.json()["status"] == "verified_success"
        memory = client.get("/health").json()["memory"]
        assert memory == {
            "experiences": 1,
            "admitted_experiences": 1,
            "knowledge": 1,
            "reflections": 1,
            "skills": 1,
        }

        events = client.get("/v1/events?limit=100")
        assert events.status_code == 200
        assert any(
            item["event_type"] == "TaskVerified" for item in events.json()["items"]
        )
        recent_events = client.get("/v1/events?latest=true&limit=2")
        assert recent_events.json()["items"] == events.json()["items"][-2:]
        assert "event_hash" in client.get("/v1/events/export").text
        task_id = code.json()["task_id"]
        assert client.get(f"/v1/tasks/{task_id}").status_code == 200
        assert client.get("/v1/tasks").json()["items"]
        assert client.get("/v1/experiences").json()["items"]
        assert client.get("/v1/knowledge").json()["items"]
        assert client.get("/v1/reflections").json()["items"]
        assert client.get("/v1/skills").json()["items"]
        assert client.get("/v1/approvals").json()["items"] == []
        assert client.get("/v1/tasks/not-found").status_code == 404


def test_human_submitted_research_curriculum_is_queued(settings) -> None:
    enabled_settings = settings.model_copy(update={"network_enabled": True})
    application = build_application(enabled_settings)
    api = create_api(application=application)

    with TestClient(api) as client:
        response = client.post(
            "/v1/research/curricula",
            json={"topic": "economics", "cycles": 2},
        )

    assert response.status_code == 202
    tasks = response.json()["tasks"]
    assert len(tasks) == 2
    assert all(task["status"] == "queued" for task in tasks)
    assert all(task["action"]["capability"] == "academic.research" for task in tasks)


def test_human_submitted_critical_research_is_queued(settings) -> None:
    enabled_settings = settings.model_copy(update={"network_enabled": True})
    application = build_application(enabled_settings)
    api = create_api(application=application)

    with TestClient(api) as client:
        response = client.post(
            "/v1/research/critical",
            json={"topic": "latest energy transition", "timespan": "7d"},
        )
        quality = client.get("/v1/research/quality")
        runs = client.get("/v1/research/runs")

    assert response.status_code == 202
    task = response.json()["task"]
    assert task["status"] == "queued"
    assert task["action"]["capability"] == "web.critical_research"
    assert quality.json()["status"] == "insufficient_history"
    assert runs.json()["items"] == []


def test_ultimate_challenge_governance_and_evaluation_api(application) -> None:
    api = create_api(application=application)
    with TestClient(api) as client:
        challenge = client.post("/v1/challenges/social-engagement")
        assert challenge.status_code == 202
        assert challenge.json()["status"] == "capability_gap"
        challenge_id = challenge.json()["challenge_id"]

        listed = client.get("/v1/challenges").json()["items"]
        assert listed[0]["challenge_id"] == challenge_id
        detail = client.get(f"/v1/challenges/{challenge_id}")
        assert detail.status_code == 200
        assert detail.json()["observations"] == []

        draft = client.post(
            "/v1/challenges/drafts",
            json={
                "goal": "建立一個可交付的應用程式",
                "completion_requirements": "通過測試並提供安裝包",
            },
        )
        assert draft.status_code == 201
        drafts = client.get("/v1/challenges/drafts").json()["items"]
        assert drafts[0]["goal"] == "建立一個可交付的應用程式"

        governance = client.post(
            "/v1/governance/autonomous-actions/evaluate",
            json={
                "action_type": "publish",
                "public_action": True,
                "ai_disclosure_present": True,
            },
        )
        assert governance.json()["allowed"]
        assert not governance.json()["requires_approval"]

        benchmark = client.post(
            "/v1/evaluations/runs",
            json={
                "suite": "gsm8k",
                "benchmark_version": "main-v1",
                "model": "mock-deterministic",
                "evaluator": "exact-match",
                "score": 0.5,
                "sample_count": 100,
            },
        )
        assert benchmark.status_code == 201
        evaluations = client.get("/v1/evaluations").json()
        assert evaluations["items"][1]["run_count"] == 1

        objective = client.post(
            "/v1/evaluations/objective",
            json={"repetitions": 2},
        )
        assert objective.status_code == 201
        assert objective.json()["run"]["score"] == 1
        comparison = client.get("/v1/evaluations/compare")
        assert comparison.status_code == 200
        assert comparison.json()["status"] == "baseline_created"


def test_mission_runtime_and_human_review_api(application) -> None:
    api = create_api(application=application)
    with TestClient(api) as client:
        runtime = client.get("/v1/runtime/status")
        assert runtime.status_code == 200
        assert runtime.json()["scheduler"] == {
            "autonomous_learning_percent": 90,
            "challenge_execution_percent": 10,
        }
        assert len(runtime.json()["skill_runtime"]["items"]) == 6

        created = client.post(
            "/v1/missions",
            json={
                "title": "建立可交付網站",
                "objective": "建立並測試一個本機網站",
                "completion_requirements": "提供原始碼、測試報告與可開啟成果",
                "source": "human",
                "schedule": "manual",
                "priority": "normal",
            },
        )
        assert created.status_code == 201
        mission_id = created.json()["mission_id"]

        updated = client.patch(
            f"/v1/missions/{mission_id}",
            json={"priority": "urgent", "objective": "建立並完整測試一個本機網站"},
        )
        assert updated.json()["priority"] == "urgent"

        submitted = client.post(
            f"/v1/missions/{mission_id}/completion",
            json={
                "result_summary": "網站與測試報告已完成",
                "evidence": ["workspace/site/index.html", "workspace/site/report.json"],
            },
        )
        assert submitted.json()["status"] == "awaiting_review"

        reviewed = client.post(
            f"/v1/missions/{mission_id}/review",
            json={"approved": True, "feedback": "成果可重現，驗收通過"},
        )
        assert reviewed.json()["status"] == "approved"
        assert reviewed.json()["approved_at"] is not None
