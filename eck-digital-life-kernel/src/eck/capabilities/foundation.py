from __future__ import annotations

import asyncio
import csv
import ipaddress
import json
import socket
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from eck.brain.base import BrainProvider
from eck.capabilities.base import Capability, CapabilityDefinition
from eck.config import Settings
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.links: list[dict[str, str]] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._href = None

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.text.append(value)
            if self._href and len(self.links) < 100:
                self.links.append({"text": value[:200], "href": self._href})


class WorkspaceCapability(Capability):
    definition = CapabilityDefinition(
        name="workspace.files",
        description="Read, list, and atomically write files inside the ECK workspace.",
        default_risk=RiskLevel.MEDIUM,
        deterministic=True,
    )

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        output: dict[str, Any]
        try:
            relative = str(action.payload.get("path", "."))
            target = self._safe_path(relative)
            if action.operation == "list":
                if not target.is_dir():
                    raise ValueError("The requested workspace path is not a directory.")
                output = {
                    "path": target.relative_to(self.workspace).as_posix(),
                    "items": [
                        {
                            "path": item.relative_to(self.workspace).as_posix(),
                            "type": "directory" if item.is_dir() else "file",
                            "bytes": item.stat().st_size if item.is_file() else None,
                        }
                        for item in sorted(target.iterdir())[:500]
                    ],
                    "metrics": {"completed": True},
                }
            elif action.operation == "read":
                if not target.is_file():
                    raise ValueError("The requested workspace file does not exist.")
                if target.stat().st_size > 2_000_000:
                    raise ValueError("Workspace reads are limited to 2 MB.")
                output = {
                    "path": target.relative_to(self.workspace).as_posix(),
                    "content": target.read_text(encoding="utf-8"),
                    "metrics": {"completed": True},
                }
            elif action.operation == "write":
                content = str(action.payload.get("content", ""))
                if len(content.encode("utf-8")) > 2_000_000:
                    raise ValueError("Workspace writes are limited to 2 MB.")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.eck-tmp")
                temporary.write_text(content, encoding="utf-8")
                temporary.replace(target)
                output = {
                    "path": target.relative_to(self.workspace).as_posix(),
                    "bytes": target.stat().st_size,
                    "metrics": {"completed": True},
                }
            else:
                raise ValueError("Workspace capability supports list, read, and write.")
            success = True
        except (OSError, UnicodeError, ValueError) as exc:
            success = False
            output = {"error": str(exc), "metrics": {"completed": False}}
        return _result(action, self.definition.name, started, success, output)

    def _safe_path(self, relative: str) -> Path:
        candidate = (self.workspace / relative).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError("Workspace path escape was blocked.")
        return candidate


class PublicWebCapability(Capability):
    definition = CapabilityDefinition(
        name="web.public_explore",
        description="Read public HTTP(S) pages with SSRF, robots, size, and social-policy gates.",
        default_risk=RiskLevel.MEDIUM,
        deterministic=False,
        network_access=True,
        autonomous_safe=True,
    )
    _social_hosts = {
        "x.com",
        "twitter.com",
        "instagram.com",
        "facebook.com",
        "tiktok.com",
        "linkedin.com",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        url = str(action.payload.get("url", ""))
        try:
            parsed = await self._validate_url(url)
            host = parsed.hostname or ""
            if any(
                host == item or host.endswith(f".{item}") for item in self._social_hosts
            ) and not action.payload.get("platform_automation_allowed"):
                raise ValueError(
                    "This social platform requires confirmed automation permission "
                    "or an official API."
                )
            headers = {"User-Agent": "ECK-Digital-Life-Kernel/0.1 public-research"}
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=False,
                headers=headers,
            ) as client:
                await self._check_robots(client, parsed)
                response = await client.get(url)
                response.raise_for_status()
            if response.is_redirect:
                raise ValueError("Redirects require a new validated request.")
            if len(response.content) > 2_000_000:
                raise ValueError("Public web responses are limited to 2 MB.")
            content_type = response.headers.get("content-type", "").lower()
            if "json" in content_type or action.operation == "get_json":
                body = response.json()
                output = {
                    "url": str(response.url),
                    "content_type": content_type,
                    "json": body,
                    "metrics": {"completed": True, "bytes": len(response.content)},
                }
            else:
                extractor = _TextExtractor()
                extractor.feed(response.text)
                output = {
                    "url": str(response.url),
                    "content_type": content_type,
                    "text": "\n".join(extractor.text)[:50000],
                    "links": [
                        {**item, "href": urljoin(url, item["href"])} for item in extractor.links
                    ],
                    "metrics": {"completed": True, "bytes": len(response.content)},
                    "policy_note": (
                        "robots.txt permitted this fetch; site terms remain authoritative."
                    ),
                }
            success = True
        except (httpx.HTTPError, json.JSONDecodeError, OSError, ValueError) as exc:
            success = False
            output = {"url": url, "error": str(exc), "metrics": {"completed": False}}
        return _result(action, self.definition.name, started, success, output)

    async def _validate_url(self, url: str) -> SplitResult:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("A public HTTP(S) URL is required.")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in URLs are prohibited.")
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ValueError(f"Non-public network address was blocked: {ip}")
        return parsed

    async def _check_robots(self, client: httpx.AsyncClient, parsed: Any) -> None:
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        response = await client.get(robots_url)
        if response.status_code == 404:
            return
        response.raise_for_status()
        if len(response.content) > 512_000:
            raise ValueError("robots.txt exceeded the 512 KB limit.")
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        if not parser.can_fetch("ECK-Digital-Life-Kernel", target):
            raise ValueError("robots.txt does not permit ECK to fetch this path.")


class PublicRestCapability(PublicWebCapability):
    definition = CapabilityDefinition(
        name="rest.public_api",
        description="Call public read-only JSON APIs with SSRF and response-size protections.",
        default_risk=RiskLevel.MEDIUM,
        deterministic=False,
        network_access=True,
        autonomous_safe=True,
    )


class DataAnalysisCapability(Capability):
    definition = CapabilityDefinition(
        name="data.analyze",
        description="Analyze CSV text or JSON records with deterministic descriptive statistics.",
        default_risk=RiskLevel.LOW,
        deterministic=True,
    )

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        try:
            records = action.payload.get("records")
            if records is None:
                csv_text = str(action.payload.get("csv", ""))
                records = list(csv.DictReader(csv_text.splitlines()))
            if not isinstance(records, list) or len(records) > 100000:
                raise ValueError("Data analysis accepts at most 100,000 records.")
            numeric: dict[str, list[float]] = {}
            for record in records:
                if not isinstance(record, dict):
                    continue
                for key, value in record.items():
                    try:
                        numeric.setdefault(str(key), []).append(float(value))
                    except (TypeError, ValueError):
                        continue
            summary = {
                key: {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "mean": fmean(values),
                }
                for key, values in numeric.items()
                if values
            }
            output = {
                "rows": len(records),
                "numeric_columns": summary,
                "metrics": {"completed": True},
            }
            success = True
        except (ValueError, TypeError) as exc:
            success = False
            output = {"error": str(exc), "metrics": {"completed": False}}
        return _result(action, self.definition.name, started, success, output)


class ArtifactPackageCapability(Capability):
    definition = CapabilityDefinition(
        name="artifact.package",
        description="Package verified workspace files into a ZIP artifact.",
        default_risk=RiskLevel.MEDIUM,
        deterministic=True,
    )

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        try:
            files = action.payload.get("files", [])
            name = Path(str(action.payload.get("name", "eck-artifact.zip"))).name
            if not name.endswith(".zip"):
                name += ".zip"
            output_dir = self.workspace / "artifacts"
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / name
            added = []
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for relative in files:
                    source = (self.workspace / str(relative)).resolve()
                    if self.workspace not in source.parents or not source.is_file():
                        raise ValueError(f"Invalid workspace artifact source: {relative}")
                    archive.write(source, source.relative_to(self.workspace))
                    added.append(source.relative_to(self.workspace).as_posix())
            output = {
                "artifact": target.relative_to(self.workspace).as_posix(),
                "files": added,
                "bytes": target.stat().st_size,
                "metrics": {"completed": True},
            }
            success = True
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            success = False
            output = {"error": str(exc), "metrics": {"completed": False}}
        return _result(action, self.definition.name, started, success, output)


class GitWorkspaceCapability(Capability):
    definition = CapabilityDefinition(
        name="git.workspace",
        description=(
            "Inspect status, diff, and history of a workspace Git repository without mutation."
        ),
        default_risk=RiskLevel.LOW,
        deterministic=False,
    )

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        output: dict[str, Any]
        commands = {
            "status": ("git", "status", "--short"),
            "diff": ("git", "diff", "--no-ext-diff", "--"),
            "log": ("git", "log", "-n", "20", "--oneline", "--decorate=no"),
        }
        command = commands.get(action.operation)
        if command is None:
            output = {"error": "Git capability supports status, diff, and log."}
            success = False
        else:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=self.workspace,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
                output = {
                    "returncode": process.returncode,
                    "output": stdout.decode("utf-8", errors="replace")[-50000:],
                    "error": stderr.decode("utf-8", errors="replace")[-4000:],
                    "metrics": {"completed": process.returncode == 0},
                }
                success = process.returncode == 0
            except (OSError, TimeoutError) as exc:
                success = False
                output = {"error": str(exc), "metrics": {"completed": False}}
        return _result(action, self.definition.name, started, success, output)


class TaskPlanningCapability(Capability):
    definition = CapabilityDefinition(
        name="task.plan",
        description="Create a bounded plan with evidence and capability requirements.",
        default_risk=RiskLevel.LOW,
        deterministic=False,
    )

    def __init__(self, brain: BrainProvider) -> None:
        self.brain = brain

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        objective = str(action.payload.get("objective", ""))[:4000]
        try:
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "使用繁體中文建立可稽核計畫。列出步驟、必要能力、外部證據、"
                            "停止條件與風險。不可宣稱尚未執行的步驟已完成。只輸出 JSON。"
                        ),
                    },
                    {"role": "user", "content": objective},
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "steps": {"type": "array", "items": {"type": "string"}},
                        "required_capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "stop_conditions": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "steps",
                        "required_capabilities",
                        "evidence",
                        "risks",
                        "stop_conditions",
                    ],
                },
            )
            plan = json.loads(response.content)
            success = isinstance(plan.get("steps"), list) and bool(plan["steps"])
            output = {**plan, "model": response.model, "metrics": {"completed": success}}
        except (json.JSONDecodeError, ValueError) as exc:
            success = False
            output = {"error": str(exc), "metrics": {"completed": False}}
        return _result(action, self.definition.name, started, success, output)


def _result(
    action: ActionProposal,
    capability: str,
    started: Any,
    success: bool,
    output: dict[str, Any],
) -> CapabilityResult:
    return CapabilityResult(
        action_id=action.action_id,
        capability=capability,
        success=success,
        output=output,
        evidence=(
            Evidence(
                source=EvidenceSource.TOOL,
                claim=f"Registered capability {capability} returned a structured result.",
                payload={"operation": action.operation, "success": success},
            ),
        ),
        reversible=action.reversible,
        cost_units=5,
        started_at=started,
        finished_at=utc_now(),
    )
