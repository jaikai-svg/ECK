from __future__ import annotations

import json
import re
from typing import Any

import httpx

from eck.brain.base import BrainProvider
from eck.config import Settings
from eck.domain.enums import MissionStatus
from eck.domain.models import MissionRecord
from eck.storage.sqlite import SQLiteStore


class MissionDevelopmentCouncil:
    """Role-separated architecture, review, and reusable-pattern support for P6."""

    _token_pattern = re.compile(r"[a-z][a-z0-9-]{2,}|[\u4e00-\u9fff]{2,8}", re.I)

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        brain: BrainProvider,
    ) -> None:
        self.settings = settings
        self.store = store
        self.brain = brain

    async def research_context(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
    ) -> dict[str, Any]:
        query = self._reference_query(mission, project_type)
        references: list[dict[str, Any]] = []
        detail = "Public reference lookup is disabled or no search results were available."
        if self.settings.network_enabled and self.settings.mission_reference_search_limit > 0:
            try:
                references = await self._search_github(query)
                detail = (
                    f"Found {len(references)} current public GitHub references."
                    if references
                    else "GitHub search returned no relevant public repositories."
                )
            except (httpx.HTTPError, json.JSONDecodeError, OSError, ValueError) as exc:
                detail = (
                    "Reference lookup failed without blocking execution: "
                    f"{type(exc).__name__}."
                )
        patterns = self.similar_patterns(mission, project_type=project_type)
        return {
            "query": query,
            "references": references,
            "reused_patterns": patterns,
            "detail": detail,
            "source_policy": (
                "References are metadata-only inspiration. ECK must not copy source code or assets "
                "without license and provenance review."
            ),
        }

    async def architecture(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
        research: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_architecture(mission, project_type, research)
        try:
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\nYou are ECK's principal software architect. "
                            "Return JSON only. Turn the mission into a concrete design system "
                            "and engineering contract before implementation. For websites, require "
                            "a responsive, content-rich, visually intentional experience with "
                            "meaningful JavaScript interaction, accessibility, empty/error/success "
                            "states, and local assets. Do not copy reference repositories. Treat "
                            "prior approved patterns as lessons, not code."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mission": mission.objective,
                                "requirements": mission.completion_requirements,
                                "project_type": project_type,
                                "research": research,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "product_direction": {"type": "string"},
                        "audience": {"type": "string"},
                        "information_architecture": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "visual_system": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "interaction_system": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "quality_risks": {"type": "array", "items": {"type": "string"}},
                        "acceptance_contract": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "product_direction",
                        "audience",
                        "information_architecture",
                        "visual_system",
                        "interaction_system",
                        "quality_risks",
                        "acceptance_contract",
                    ],
                },
                options={"temperature": 0.15, "num_predict": 1500},
            )
            candidate = self._json_object(response.content)
            if all(
                isinstance(candidate.get(key), list) and len(candidate[key]) >= 3
                for key in (
                    "information_architecture",
                    "visual_system",
                    "interaction_system",
                    "acceptance_contract",
                )
            ):
                fallback.update(candidate)
                fallback["model"] = response.model
        except (json.JSONDecodeError, RuntimeError, ValueError):
            pass
        return fallback

    async def implementation_plan(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
        architecture: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_plan(project_type)
        try:
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\nYou are ECK's implementation planner. Return JSON only. "
                            "Apply the Superpowers planning principles: exact files, explicit "
                            "interfaces, small independently reviewable tasks, concrete checks, "
                            "no TODO placeholders, "
                            "and evidence before completion. Produce at least six ordered tasks."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mission": mission.objective,
                                "requirements": mission.completion_requirements,
                                "project_type": project_type,
                                "architecture": architecture,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "task_id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "files": {"type": "array", "items": {"type": "string"}},
                                    "objective": {"type": "string"},
                                    "interfaces": {"type": "array", "items": {"type": "string"}},
                                    "checks": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": [
                                    "task_id",
                                    "title",
                                    "files",
                                    "objective",
                                    "interfaces",
                                    "checks",
                                ],
                            },
                        }
                    },
                    "required": ["tasks"],
                },
                options={"temperature": 0.1, "num_predict": 2200},
            )
            candidate = self._json_object(response.content)
            tasks = candidate.get("tasks")
            if isinstance(tasks, list) and len(tasks) >= 6:
                fallback = {"tasks": tasks, "model": response.model, "method": "model-plan"}
        except (json.JSONDecodeError, RuntimeError, ValueError):
            pass
        return fallback

    async def expert_review(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
        round_number: int,
        architecture: dict[str, Any],
        files: list[dict[str, str]],
        deterministic: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_review(
            project_type=project_type,
            round_number=round_number,
            deterministic=deterministic,
            human_feedback=mission.review_feedback,
        )
        try:
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\nYou are ECK's independent principal reviewer. Return JSON "
                            "only. You did not implement this work. Be demanding and "
                            "evidence-specific. Review spec compliance and expert quality "
                            "separately. Never approve because files exist. Find concrete issues "
                            "in content, visual hierarchy, responsive behavior, interaction, "
                            "accessibility, maintainability, and task relevance. Provide at least "
                            "five actionable findings, each with location, evidence, required "
                            "change, and an objective acceptance check. A prior human comment is "
                            "binding input."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mission": mission.objective,
                                "requirements": mission.completion_requirements,
                                "human_feedback": mission.review_feedback,
                                "project_type": project_type,
                                "review_round": round_number,
                                "architecture": architecture,
                                "deterministic_evidence": deterministic,
                                "files": files,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "scores": {
                            "type": "object",
                            "properties": {
                                "spec": {"type": "number"},
                                "content": {"type": "number"},
                                "visual": {"type": "number"},
                                "interaction": {"type": "number"},
                                "accessibility": {"type": "number"},
                                "maintainability": {"type": "number"},
                            },
                            "required": [
                                "spec",
                                "content",
                                "visual",
                                "interaction",
                                "accessibility",
                                "maintainability",
                            ],
                        },
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "severity": {"type": "string"},
                                    "location": {"type": "string"},
                                    "evidence": {"type": "string"},
                                    "required_change": {"type": "string"},
                                    "acceptance_check": {"type": "string"},
                                },
                                "required": [
                                    "severity",
                                    "location",
                                    "evidence",
                                    "required_change",
                                    "acceptance_check",
                                ],
                            },
                        },
                    },
                    "required": ["summary", "scores", "findings"],
                },
                options={"temperature": 0.05, "num_predict": 2400},
            )
            candidate = self._json_object(response.content)
            findings = candidate.get("findings")
            scores = candidate.get("scores")
            if isinstance(findings, list) and len(findings) >= 5 and isinstance(scores, dict):
                normalized_scores = {
                    key: max(0.0, min(100.0, float(scores.get(key, 0))))
                    for key in (
                        "spec",
                        "content",
                        "visual",
                        "interaction",
                        "accessibility",
                        "maintainability",
                    )
                }
                fallback.update(candidate)
                fallback["scores"] = normalized_scores
                fallback["score"] = round(
                    sum(normalized_scores.values()) / len(normalized_scores),
                    1,
                )
                fallback["model"] = response.model
        except (json.JSONDecodeError, RuntimeError, TypeError, ValueError):
            pass
        fallback["round"] = round_number
        fallback["minimum_score"] = self.settings.mission_quality_min_score
        fallback["passed"] = bool(
            fallback.get("score", 0) >= self.settings.mission_quality_min_score
            and not any(
                str(item.get("severity", "")).casefold() == "critical"
                for item in fallback.get("findings", [])
                if isinstance(item, dict)
            )
        )
        return fallback

    async def improve(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
        round_number: int,
        phase: str,
        architecture: dict[str, Any],
        review: dict[str, Any],
        files: list[dict[str, str]],
    ) -> dict[str, Any]:
        try:
            response = await self.brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\nYou are ECK's senior implementation engineer. Return JSON "
                            "only. Apply every expert finding to the complete project, preserve "
                            "working behavior, and return every complete file rather than patches. "
                            "For websites, improve real content, responsive composition, visual "
                            "polish, keyboard accessibility, and meaningful dynamic interaction. "
                            "No external CDN, telemetry, TODO, placeholder, "
                            "or fake success. Do not merely add comments to claim improvement."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mission": mission.objective,
                                "requirements": mission.completion_requirements,
                                "human_feedback": mission.review_feedback,
                                "project_type": project_type,
                                "improvement_phase": phase,
                                "improvement_round": round_number,
                                "architecture": architecture,
                                "review": review,
                                "current_files": files,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "addressed_findings": {"type": "array", "items": {"type": "string"}},
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
                    "required": ["addressed_findings", "files"],
                },
                options={"temperature": 0.12, "num_predict": 8192},
            )
            candidate = self._json_object(response.content)
            if isinstance(candidate.get("files"), list):
                candidate["model"] = response.model
                return candidate
        except (json.JSONDecodeError, RuntimeError, ValueError):
            pass
        return {
            "addressed_findings": [],
            "files": [],
            "model": "deterministic-improvement-fallback.v1",
        }

    def distill_pattern(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
        architecture: dict[str, Any],
        reviews: list[dict[str, Any]],
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        lessons: list[str] = []
        for review in reviews:
            for finding in review.get("findings", []):
                if isinstance(finding, dict):
                    change = str(finding.get("required_change", "")).strip()
                    if change and change not in lessons:
                        lessons.append(change)
        return {
            "schema_version": "eck-mission-pattern.v1",
            "project_type": project_type,
            "title": mission.title,
            "tags": sorted(self._tokens(f"{mission.title} {mission.objective}"))[:20],
            "architecture": {
                "product_direction": architecture.get("product_direction", ""),
                "visual_system": architecture.get("visual_system", []),
                "interaction_system": architecture.get("interaction_system", []),
            },
            "review_lessons": lessons[:20],
            "quality_score": validation.get("quality_score", 0),
            "source_sha256": validation.get("source_sha256", ""),
            "activation_policy": "Reusable only after this mission receives human approval.",
        }

    def similar_patterns(
        self,
        mission: MissionRecord,
        *,
        project_type: str,
    ) -> list[dict[str, Any]]:
        target_tokens = self._tokens(f"{mission.title} {mission.objective}")
        ranked: list[tuple[float, dict[str, Any]]] = []
        for prior in self.store.list_missions(limit=1000):
            if prior.mission_id == mission.mission_id or prior.status is not MissionStatus.APPROVED:
                continue
            pattern = prior.progress.get("learning_pattern")
            if not isinstance(pattern, dict) or pattern.get("project_type") != project_type:
                continue
            tags = {str(item).casefold() for item in pattern.get("tags", [])}
            overlap = len(target_tokens & tags)
            score = 2.0 + overlap / max(len(target_tokens), 1)
            ranked.append((score, pattern))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [pattern for _, pattern in ranked[:3]]

    async def _search_github(self, query: str) -> list[dict[str, Any]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "ECK-Digital-Life-Kernel/0.1 mission-reference-research",
        }
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers=headers) as client:
            response = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": query[:240],
                    "sort": "stars",
                    "order": "desc",
                    "per_page": self.settings.mission_reference_search_limit,
                },
            )
            response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        references: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            references.append(
                {
                    "name": str(item.get("full_name", ""))[:200],
                    "url": str(item.get("html_url", ""))[:1000],
                    "description": str(item.get("description") or "")[:1000],
                    "stars": int(item.get("stargazers_count") or 0),
                    "language": str(item.get("language") or "")[:80],
                    "license": str((item.get("license") or {}).get("spdx_id") or "UNKNOWN")[:80],
                    "updated_at": str(item.get("updated_at") or "")[:80],
                }
            )
        return references

    def _reference_query(self, mission: MissionRecord, project_type: str) -> str:
        text = f"{mission.title} {mission.objective}".casefold()
        if re.search(r"旅遊|旅行|travel", text, re.I):
            focus = "travel website frontend"
        else:
            latin = [token for token in self._tokens(text) if token.isascii()]
            focus = " ".join(latin[:4]) or "modern software interface"
        qualifier = "language:JavaScript" if project_type == "static_website" else "language:Python"
        return f"{focus} {qualifier} stars:>50 archived:false"

    def _fallback_architecture(
        self,
        mission: MissionRecord,
        project_type: str,
        research: dict[str, Any],
    ) -> dict[str, Any]:
        website = project_type == "static_website"
        return {
            "product_direction": (
                f"Build a distinctive, trustworthy experience for: {mission.objective}"
            ),
            "audience": (
                "People who need to understand the offer and complete one meaningful action."
            ),
            "information_architecture": [
                "Immediate value proposition and primary action",
                "Structured evidence-rich content sections",
                "Interactive decision or exploration tool",
                "Trust, provenance, and completion state",
            ],
            "visual_system": [
                "Purposeful color tokens and typography hierarchy",
                "Responsive grid with deliberate spacing rhythm",
                "Original local visual treatment without copied assets",
                "Visible hover, focus, selected, and reduced-motion states",
            ],
            "interaction_system": [
                "Keyboard-operable navigation and controls",
                "At least three meaningful event-driven interactions",
                "Dynamic content update with accessible live feedback",
                "Mobile navigation that preserves state",
            ],
            "quality_risks": [
                "Generic template copy",
                "Decorative-only JavaScript",
                "Weak mobile composition",
                "Model self-approval without deterministic evidence",
            ],
            "acceptance_contract": [
                "No placeholders or broken local references",
                "Deterministic validator reaches the configured quality score",
                "Three independent expert review and improvement rounds are persisted",
                "Human approval remains the final completion gate",
            ],
            "project_type": project_type,
            "reference_count": len(research.get("references", [])),
            "model": "deterministic-principal-architect.v1",
            "website": website,
        }

    @staticmethod
    def _fallback_plan(project_type: str) -> dict[str, Any]:
        site = project_type == "static_website"
        tasks = (
            [
                (
                    "01",
                    "Define semantic structure",
                    ["index.html"],
                    "Create complete content hierarchy",
                ),
                (
                    "02",
                    "Build design tokens",
                    ["styles.css"],
                    "Define color, type, spacing, and states",
                ),
                (
                    "03",
                    "Compose responsive layouts",
                    ["styles.css"],
                    "Implement desktop and mobile grids",
                ),
                (
                    "04",
                    "Implement real interactions",
                    ["app.js"],
                    "Add stateful accessible behavior",
                ),
                (
                    "05",
                    "Write specific content",
                    ["index.html"],
                    "Replace generic claims with useful details",
                ),
                (
                    "06",
                    "Verify and document",
                    ["README.md"],
                    "Record execution and objective checks",
                ),
            ]
            if site
            else [
                (
                    "01",
                    "Define public interfaces",
                    ["README.md"],
                    "Document exact inputs and outputs",
                ),
                (
                    "02",
                    "Write failing behavior tests",
                    ["tests/test_app.py"],
                    "Prove required behavior",
                ),
                (
                    "03",
                    "Implement core domain",
                    ["mission_app.py"],
                    "Satisfy the smallest complete contract",
                ),
                (
                    "04",
                    "Handle invalid inputs",
                    ["mission_app.py"],
                    "Add explicit failure behavior",
                ),
                (
                    "05",
                    "Extend edge-case tests",
                    ["tests/test_app.py"],
                    "Cover boundaries without mocks",
                ),
                (
                    "06",
                    "Document reproducibility",
                    ["README.md"],
                    "Provide exact local verification steps",
                ),
            ]
        )
        return {
            "tasks": [
                {
                    "task_id": task_id,
                    "title": title,
                    "files": files,
                    "objective": objective,
                    "interfaces": ["Consumes the approved architecture contract"],
                    "checks": [
                        "Produces a separately reviewable artifact",
                        "Contains no placeholder",
                    ],
                }
                for task_id, title, files, objective in tasks
            ],
            "model": "deterministic-superpowers-plan.v1",
            "method": "bite-sized-plan",
        }

    def _fallback_review(
        self,
        *,
        project_type: str,
        round_number: int,
        deterministic: dict[str, Any],
        human_feedback: str,
    ) -> dict[str, Any]:
        score = float(deterministic.get("quality_score", 0))
        if project_type == "static_website":
            findings = [
                self._finding(
                    "important",
                    "index.html",
                    "Content hierarchy needs stronger specificity.",
                    "Add concrete, task-specific sections and calls to action.",
                    "At least five semantic content sections exist.",
                ),
                self._finding(
                    "important",
                    "styles.css",
                    "Visual system must be deliberate rather than browser-default.",
                    "Strengthen tokens, typography, spacing, and responsive composition.",
                    "CSS quality metrics and responsive checks pass.",
                ),
                self._finding(
                    "important",
                    "app.js",
                    "Interaction must change meaningful page state.",
                    "Add accessible stateful interactions beyond navigation.",
                    "At least three event listeners and a live state update exist.",
                ),
                self._finding(
                    "important",
                    "index.html",
                    "Accessibility requires explicit labels and live feedback.",
                    "Add landmarks, labels, focus order, and aria-live feedback.",
                    "Accessibility contract passes deterministic inspection.",
                ),
                self._finding(
                    "minor",
                    "README.md",
                    "Delivery rationale and verification evidence should be reproducible.",
                    "Document architecture, interactions, and local checks.",
                    "README records the implemented contract.",
                ),
            ]
        else:
            findings = [
                self._finding(
                    "important",
                    "source",
                    "Domain behavior must match the objective.",
                    "Use objective-specific names and behavior.",
                    "Static relevance checks pass.",
                ),
                self._finding(
                    "important",
                    "tests",
                    "Tests must cover real behavior.",
                    "Add deterministic assertions without mocks.",
                    "Docker pytest succeeds.",
                ),
                self._finding(
                    "important",
                    "source",
                    "Invalid input behavior needs explicit contracts.",
                    "Validate boundaries and raise documented errors.",
                    "Edge-case tests pass.",
                ),
                self._finding(
                    "minor",
                    "README.md",
                    "Usage must be reproducible.",
                    "Document inputs, outputs, and test command.",
                    "README includes executable examples.",
                ),
                self._finding(
                    "minor",
                    "architecture",
                    "Interfaces should remain focused.",
                    "Reduce unnecessary coupling.",
                    "Static quality gate reports no architecture issue.",
                ),
            ]
        if human_feedback:
            findings[0] = self._finding(
                "critical",
                "human-feedback",
                human_feedback,
                "Implement the creator's feedback before resubmission.",
                "The next review explicitly verifies the requested correction.",
            )
        base = max(0.0, min(100.0, score))
        return {
            "summary": (
                f"Independent quality review round {round_number} identified required "
                "refinements."
            ),
            "scores": {
                key: base
                for key in (
                    "spec",
                    "content",
                    "visual",
                    "interaction",
                    "accessibility",
                    "maintainability",
                )
            },
            "score": base,
            "findings": findings,
            "model": "deterministic-demanding-reviewer.v1",
        }

    @staticmethod
    def _finding(
        severity: str,
        location: str,
        evidence: str,
        required_change: str,
        acceptance_check: str,
    ) -> dict[str, str]:
        return {
            "severity": severity,
            "location": location,
            "evidence": evidence,
            "required_change": required_change,
            "acceptance_check": acceptance_check,
        }

    def _tokens(self, value: str) -> set[str]:
        return {token.casefold() for token in self._token_pattern.findall(value)}

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise
            value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("Model response must be a JSON object.")
        return value
