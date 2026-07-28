from __future__ import annotations

from fastapi.testclient import TestClient

from eck.api.main import create_api


def test_health_dashboard_and_acceptance(application) -> None:
    api = create_api(application=application)
    with TestClient(api) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Digital Life Kernel" in dashboard.text

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["event_chain"]["valid"]

        assert client.get("/v1/kernel/status").status_code == 200
        assert client.post("/v1/kernel/start").json()["phase"] == "running"
        assert client.post("/v1/kernel/pause").json()["phase"] == "paused"
        assert client.post("/v1/kernel/resume").json()["phase"] == "running"
        assert client.post("/v1/kernel/sleep").json()["accepted"]
        assert len(client.get("/v1/capabilities").json()["items"]) == 2

        code = client.post("/v1/demos/safe-code")
        assert code.status_code == 200
        assert code.json()["status"] == "verified_success"
        memory = client.get("/health").json()["memory"]
        assert memory == {
            "experiences": 1,
            "knowledge": 1,
            "reflections": 1,
            "skills": 1,
        }

        events = client.get("/v1/events?limit=100")
        assert events.status_code == 200
        assert any(
            item["event_type"] == "TaskVerified" for item in events.json()["items"]
        )
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
