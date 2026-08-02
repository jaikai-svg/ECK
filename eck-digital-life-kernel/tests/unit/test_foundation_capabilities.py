from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from eck.brain.base import BrainHealth, BrainProvider, BrainResponse
from eck.capabilities.foundation import (
    ArtifactPackageCapability,
    DataAnalysisCapability,
    GitWorkspaceCapability,
    PublicWebCapability,
    TaskPlanningCapability,
    WorkspaceCapability,
)
from eck.domain.models import ActionProposal


class _PlanBrain(BrainProvider):
    def __init__(self, content: str) -> None:
        self.content = content

    async def health(self) -> BrainHealth:
        return BrainHealth(provider="test", available=True, model="test")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_schema: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> BrainResponse:
        return BrainResponse(content=self.content, model="test", raw={})


class _Response:
    def __init__(
        self,
        *,
        url: str,
        content: bytes,
        content_type: str,
        status_code: int = 200,
    ) -> None:
        self.url = url
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = status_code
        self.is_redirect = False
        self.text = content.decode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400 and self.status_code != 404:
            raise httpx.HTTPStatusError("failed", request=None, response=None)

    def json(self) -> Any:
        return json.loads(self.content)


class _WebClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> _WebClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str) -> _Response:
        if url.endswith("/robots.txt"):
            return _Response(
                url=url,
                content=b"not found",
                content_type="text/plain",
                status_code=404,
            )
        if url.endswith("/data"):
            return _Response(
                url=url,
                content=b'{"verified": true}',
                content_type="application/json",
            )
        return _Response(
            url=url,
            content=b'<html><body>Verified <a href="/next">source</a></body></html>',
            content_type="text/html; charset=utf-8",
        )


@pytest.mark.asyncio
async def test_workspace_data_package_and_git_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceCapability(workspace)

    written = await files.execute(
        ActionProposal(
            capability="workspace.files",
            operation="write",
            payload={"path": "reports/value.txt", "content": "verified"},
        )
    )
    read = await files.execute(
        ActionProposal(
            capability="workspace.files",
            operation="read",
            payload={"path": "reports/value.txt"},
        )
    )
    listed = await files.execute(
        ActionProposal(capability="workspace.files", operation="list", payload={"path": "."})
    )
    escaped = await files.execute(
        ActionProposal(
            capability="workspace.files",
            operation="read",
            payload={"path": "../secret.txt"},
        )
    )

    assert written.success and read.output["content"] == "verified"
    assert listed.success and listed.output["items"][0]["type"] == "directory"
    assert not escaped.success

    analyzed = await DataAnalysisCapability().execute(
        ActionProposal(
            capability="data.analyze",
            operation="analyze",
            payload={"csv": "value,label\n1,a\n3,b"},
        )
    )
    invalid_data = await DataAnalysisCapability().execute(
        ActionProposal(
            capability="data.analyze",
            operation="analyze",
            payload={"records": "not-a-list"},
        )
    )
    assert analyzed.output["numeric_columns"]["value"]["mean"] == 2
    assert not invalid_data.success

    package = ArtifactPackageCapability(workspace)
    packaged = await package.execute(
        ActionProposal(
            capability="artifact.package",
            operation="package",
            payload={"name": "bundle", "files": ["reports/value.txt"]},
        )
    )
    invalid_package = await package.execute(
        ActionProposal(
            capability="artifact.package",
            operation="package",
            payload={"name": "bad", "files": ["../secret.txt"]},
        )
    )
    assert packaged.success and packaged.output["artifact"].endswith("bundle.zip")
    assert not invalid_package.success

    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    git_capability = GitWorkspaceCapability(workspace)
    status = await git_capability.execute(
        ActionProposal(capability="git.workspace", operation="status", payload={})
    )
    unsupported = await git_capability.execute(
        ActionProposal(capability="git.workspace", operation="commit", payload={})
    )
    assert status.success
    assert not unsupported.success


@pytest.mark.asyncio
async def test_public_web_html_json_and_policy_gates(settings, monkeypatch) -> None:
    capability = PublicWebCapability(settings.model_copy(update={"network_enabled": True}))

    async def public_url(url: str):
        return urlsplit(url)

    monkeypatch.setattr(capability, "_validate_url", public_url)
    monkeypatch.setattr(httpx, "AsyncClient", _WebClient)

    html = await capability.execute(
        ActionProposal(
            capability="web.public_explore",
            operation="get",
            payload={"url": "https://example.com/page"},
        )
    )
    data = await capability.execute(
        ActionProposal(
            capability="web.public_explore",
            operation="get_json",
            payload={"url": "https://example.com/data"},
        )
    )
    social = await capability.execute(
        ActionProposal(
            capability="web.public_explore",
            operation="get",
            payload={"url": "https://x.com/post"},
        )
    )

    assert html.success and html.output["links"][0]["href"] == "https://example.com/next"
    assert data.success and data.output["json"]["verified"] is True
    assert not social.success and "automation permission" in social.output["error"]

    validation_capability = PublicWebCapability(settings)
    with pytest.raises(ValueError, match="public HTTP"):
        await validation_capability._validate_url("file:///tmp/test")


@pytest.mark.asyncio
async def test_task_planner_accepts_structured_plan_and_rejects_invalid_json() -> None:
    content = json.dumps(
        {
            "steps": ["建立契約", "執行驗證"],
            "required_capabilities": ["workspace.files"],
            "evidence": ["tool output"],
            "risks": ["timeout"],
            "stop_conditions": ["contract failed"],
        },
        ensure_ascii=False,
    )
    planned = await TaskPlanningCapability(_PlanBrain(content)).execute(
        ActionProposal(
            capability="task.plan",
            operation="plan",
            payload={"objective": "建立可驗證計畫"},
        )
    )
    invalid = await TaskPlanningCapability(_PlanBrain("not json")).execute(
        ActionProposal(
            capability="task.plan",
            operation="plan",
            payload={"objective": "invalid"},
        )
    )

    assert planned.success and planned.output["metrics"]["completed"]
    assert not invalid.success
