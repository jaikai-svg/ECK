from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

from eck.domain.models import DevelopmentProjectRequest
from eck.services.project_lab_components.base import ProjectLabMixinBase


class ProjectLabDraftingMixin(ProjectLabMixinBase):
    async def _draft(
        self,
        request: DevelopmentProjectRequest,
        research: list[dict[str, Any]],
        *,
        feedback: str | None = None,
        previous_draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence = [self._compact_research_evidence(item) for item in research]
        response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are ECK's autonomous project engineer. Build one small Python 3.11 "
                        "project from verified research. Use only the standard library. Include "
                        "at least one executable .py file outside tests and at least one test at "
                        "the exact path pattern tests/test_*.py. Tests must run from the project "
                        "root with python -m pytest -q. Include a useful README. Do not claim "
                        "results that tests cannot prove. Do not include credentials, network "
                        "calls, "
                        "subprocess, "
                        "shell commands, package installation, telemetry, or hidden downloads. "
                        "Do not use random, current time, placeholders, mocked success, or "
                        "simulated "
                        "measurements. Tests must assert exact behavior or numeric invariants, not "
                        "only types or non-null values. Do not include requirements.txt because "
                        "the project must use only the standard library. Tests must execute the "
                        "real implementation and may not mock or patch it. "
                        "Use at least two distinctive words from the objective in meaningful "
                        "function, class, or module names so the implementation is audibly tied "
                        "to the requested research topic. "
                        "When previous-attempt feedback is present, repair those files minimally, "
                        "preserve Python newlines and indentation, and return every complete file. "
                        "Return complete files, not prose patches."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "research": evidence,
                            "previous_attempt_feedback": feedback,
                            "previous_attempt": self._compact_previous_draft(previous_draft),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 4096},
            format_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                "required": ["name", "summary", "files"],
            },
        )
        payload = self._json_object(response.content)
        payload["model"] = response.model
        return payload

    async def _draft_split(
        self,
        request: DevelopmentProjectRequest,
        research: list[dict[str, Any]],
        *,
        feedback: str,
        previous_draft: dict[str, Any] | None,
    ) -> dict[str, Any]:
        context = {
            "objective": request.objective,
            "research": [self._compact_research_evidence(item) for item in research],
            "failure": feedback,
            "previous_attempt": self._compact_previous_draft(previous_draft),
        }
        source_response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Write the complete raw contents of experiment.py for one small, "
                        "deterministic Python 3.11 experiment. Return Python only, without a "
                        "Markdown fence or explanation. Use only the standard library. Do not use "
                        "random, time, network, subprocess, external files, placeholders, "
                        "simulated "
                        "success, input(), or package installation. Implement measurable behavior "
                        "with stable inputs and outputs that tests can verify exactly. Use at "
                        "least "
                        "two distinctive objective words in meaningful function or class names."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        source = self._plain_code(source_response.content)
        test_response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Write the complete raw contents of tests/test_experiment.py for the "
                        "provided experiment.py. Return Python only, without a Markdown fence or "
                        "explanation. Import from experiment. Include at least two behavioral "
                        "assertions with exact expected values or numeric invariants. Do not use "
                        "only type, truthiness, or non-null assertions. Use no network or files."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "experiment.py": source[:8000],
                            "previous_failure": feedback,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        tests = self._plain_code(test_response.content)
        suffix = hashlib.sha256(request.objective.encode("utf-8")).hexdigest()[:10]
        return {
            "name": f"eck-experiment-{suffix}",
            "summary": (
                "A bounded split-file repair generated from verified research and validated "
                "against deterministic behavior."
            ),
            "files": [
                {"path": "experiment.py", "content": source},
                {"path": "tests/test_experiment.py", "content": tests},
            ],
            "model": source_response.model,
        }

    async def _repair_split_tests(
        self,
        request: DevelopmentProjectRequest,
        source: str,
        *,
        failure: str,
    ) -> str:
        tree = ast.parse(source, filename="experiment.py")
        functions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        response = await self.coder_brain.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Repair only tests/test_experiment.py. Return complete raw Python without "
                        "a Markdown fence or explanation. Import only real names listed in "
                        "available_functions from experiment. Include at least two deterministic "
                        "behavioral assertions using exact expected values or numeric invariants. "
                        "Do not use mocks, files, network, external packages, type-only checks, "
                        "truthiness-only checks, or names absent from experiment.py."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": request.objective,
                            "available_functions": functions,
                            "experiment.py": source[:8000],
                            "pytest_failure": failure[-3000:],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        return self._plain_code(response.content)

    @staticmethod
    def _plain_code(content: str) -> str:
        cleaned = content.strip()
        fenced = re.search(r"```(?:python)?\s*(.*?)```", cleaned, flags=re.I | re.S)
        if fenced:
            cleaned = fenced.group(1).strip()
        return cleaned + "\n" if cleaned else ""

    @staticmethod
    def _compact_previous_draft(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        remaining = 8000
        files: list[dict[str, str]] = []
        for item in value.get("files", []):
            if not isinstance(item, dict) or remaining <= 0:
                continue
            path = str(item.get("path", ""))[:300]
            content = str(item.get("content", ""))[: min(remaining, 4000)]
            remaining -= len(content)
            files.append({"path": path, "content": content})
        return {
            "name": str(value.get("name", ""))[:100],
            "summary": str(value.get("summary", ""))[:500],
            "files": files,
        }

    @staticmethod
    def _compact_research_evidence(item: dict[str, Any]) -> dict[str, Any]:
        claims = [
            {
                "claim": str(claim.get("claim", ""))[:400],
                "status": str(claim.get("status", ""))[:40],
                "confidence": claim.get("confidence"),
            }
            for claim in item.get("claims", [])[:3]
            if isinstance(claim, dict)
        ]
        sources = [
            {
                "canonical_url": str(source.get("canonical_url", ""))[:300],
                "title": str(source.get("title", ""))[:180],
                "source_domain": str(source.get("source_domain", ""))[:120],
                "published_at": str(source.get("published_at", ""))[:40],
            }
            for source in item.get("sources", [])[:3]
            if isinstance(source, dict)
        ]
        return {
            "run_id": str(item.get("run_id", "")),
            "topic": str(item.get("topic", ""))[:300],
            "conclusion": str(item.get("conclusion", ""))[:600],
            "claims": claims,
            "sources": sources,
        }


