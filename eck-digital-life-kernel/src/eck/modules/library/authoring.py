from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from eck.config import Settings
from eck.domain.enums import TaskStatus
from eck.domain.models import MissionCreate, TaskRecord
from eck.modules.library.quality import normalize_claim, unresolved_questions
from eck.services.missions import MissionService
from eck.storage.sqlite import SQLiteStore


class LibraryReadinessError(RuntimeError):
    pass


class LibraryAuthoringService:
    """Maturity-gated formal authoring over authoritative verified knowledge."""

    relation_types = {
        "prerequisite",
        "extension",
        "supports",
        "contradicts",
        "application",
        "similar",
        "cross_domain",
    }

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        missions: MissionService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.missions = missions

    def default_thresholds(self) -> dict[str, Any]:
        return {
            "min_cards": self.settings.library_min_cards,
            "min_chapters": self.settings.library_min_chapters,
            "min_relation_coverage": self.settings.library_min_relation_coverage,
            "min_independent_source_ratio": (
                self.settings.library_min_independent_source_ratio
            ),
            "min_applied_tasks": self.settings.library_min_applied_tasks,
            "min_fixed_tests": self.settings.library_min_fixed_tests,
            "min_hidden_tests": self.settings.library_min_hidden_tests,
            "min_evaluation_score": self.settings.library_min_evaluation_score,
        }

    def create_domain(
        self,
        *,
        title: str,
        description: str,
        knowledge_selector: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        frozen = {**self.default_thresholds(), **(thresholds or {})}
        self._validate_thresholds(frozen)
        domain = self.store.create_library_domain(
            {
                "slug": self._slug(title),
                "title": title.strip(),
                "description": description.strip(),
                "knowledge_selector": knowledge_selector,
                "thresholds": frozen,
            }
        )
        self.sync_domain(str(domain["domain_id"]))
        return self.domain(str(domain["domain_id"]))

    def sync_domain(self, domain_id: str) -> int:
        domain = self.store.get_library_domain(domain_id)
        selector = domain["knowledge_selector"]
        explicit = {str(item) for item in selector.get("knowledge_ids", [])}
        prefixes = tuple(str(item) for item in selector.get("capability_prefixes", []))
        query = str(selector.get("query", "")).casefold().strip()
        knowledge = self.store.list_knowledge(limit=10000)
        existing_ids = self.store.list_domain_knowledge_ids(domain_id)
        existing_rank = {knowledge_id: index for index, knowledge_id in enumerate(existing_ids)}
        grouped: dict[str, list[Any]] = {}
        for item in knowledge:
            if not item.admitted:
                continue
            selected = item.knowledge_id in explicit
            selected = selected or bool(prefixes and item.capability.startswith(prefixes))
            selected = selected or bool(
                query and query in f"{item.capability} {item.claim}".casefold()
            )
            if selected:
                grouped.setdefault(normalize_claim(item.claim), []).append(item)
        selected_ids: list[str] = []
        for candidates in grouped.values():
            candidates.sort(
                key=lambda item: (
                    existing_rank.get(item.knowledge_id, len(existing_rank)),
                    item.created_at,
                )
            )
            selected_ids.append(candidates[0].knowledge_id)
        self.store.replace_domain_cards(domain_id, selected_ids)
        return len(selected_ids)

    def add_relation(
        self,
        *,
        source_knowledge_id: str,
        target_knowledge_id: str,
        relation_type: str,
        rationale: str,
        evidence_ids: list[str],
        verified: bool,
    ) -> dict[str, Any]:
        if relation_type not in self.relation_types:
            raise ValueError(f"Unsupported relation type: {relation_type}")
        if source_knowledge_id == target_knowledge_id:
            raise ValueError("A knowledge card cannot relate to itself.")
        return self.store.create_knowledge_relation(
            {
                "source_knowledge_id": source_knowledge_id,
                "target_knowledge_id": target_knowledge_id,
                "relation_type": relation_type,
                "rationale": rationale,
                "evidence_ids": evidence_ids,
                "verified": verified,
            }
        )

    def domains(self) -> dict[str, Any]:
        return {
            "items": [
                self.domain(str(item["domain_id"]))
                for item in self.store.list_library_domains()
            ],
            "source_authority": "knowledge_items + tasks + reflections",
        }

    def domain(self, domain_id: str) -> dict[str, Any]:
        domain = self.store.get_library_domain(domain_id)
        cards = self.cards(domain_id)
        report = self.store.latest_readiness_report(domain_id)
        books = self.store.list_library_books(domain_id)
        return {
            **domain,
            "cards": cards,
            "card_count": len(cards),
            "readiness": report,
            "books": [self._book_summary(item) for item in books],
        }

    def cards(self, domain_id: str) -> list[dict[str, Any]]:
        knowledge = {
            item.knowledge_id: item for item in self.store.list_knowledge(limit=10000)
        }
        reflection_by_task = {
            item.task_id: item for item in self.store.list_reflections(limit=10000)
        }
        ids = self.store.list_domain_knowledge_ids(domain_id)
        relations = self.store.list_knowledge_relations(ids)
        by_card: dict[str, list[dict[str, Any]]] = {item: [] for item in ids}
        for relation in relations:
            by_card.setdefault(str(relation["source_knowledge_id"]), []).append(relation)
            by_card.setdefault(str(relation["target_knowledge_id"]), []).append(relation)
        cards = []
        seen_claims: set[str] = set()
        for knowledge_id in ids:
            item = knowledge.get(knowledge_id)
            if item is None or not item.admitted:
                continue
            claim_key = normalize_claim(item.claim)
            if claim_key in seen_claims:
                continue
            seen_claims.add(claim_key)
            task = self._task(item.task_id)
            reflection = reflection_by_task.get(item.task_id)
            sources = self._sources(task)
            failed_checks = (
                list(task.verification.failed_checks)
                if task and task.verification
                else []
            )
            cards.append(
                {
                    "knowledge_id": item.knowledge_id,
                    "task_id": item.task_id,
                    "title": self._title(item.claim),
                    "core_claim": item.claim,
                    "explanation": reflection.lesson if reflection else item.claim,
                    "capability": item.capability,
                    "chapter": self._chapter(item.capability),
                    "sources": sources,
                    "confidence": (
                        float(task.verification.score) if task and task.verification else 0.0
                    ),
                    "applicability": task.goal if task else "",
                    "counterexamples": failed_checks,
                    "unresolved_questions": unresolved_questions(
                        reflection.next_step if reflection else ""
                    ),
                    "verification_status": item.outcome.value,
                    "created_at": item.created_at.isoformat(),
                    "content_sha256": self._hash(item.model_dump(mode="json")),
                    "relations": by_card.get(knowledge_id, []),
                    "skill_usages": self.store.list_task_skill_usages(
                        task_id=item.task_id,
                        limit=100,
                    ),
                }
            )
        return cards

    def evaluate(self, domain_id: str) -> dict[str, Any]:
        self.sync_domain(domain_id)
        domain = self.store.get_library_domain(domain_id)
        cards = self.cards(domain_id)
        thresholds = dict(domain["thresholds"])
        relations = self.store.list_knowledge_relations(
            [str(item["knowledge_id"]) for item in cards]
        )
        related = {
            str(item[key])
            for item in relations
            if item["verified"]
            for key in ("source_knowledge_id", "target_knowledge_id")
        }
        independent = sum(
            1 for card in cards if len(self._independent_sources(card["sources"])) >= 2
        )
        tasks = self.store.list_tasks_with_label(
            f"library-domain:{domain['slug']}", limit=5000
        )
        successful = [
            task for task in tasks if task.status is TaskStatus.VERIFIED_SUCCESS
        ]
        fixed = [task for task in successful if "library-fixed-evaluation" in task.labels]
        hidden = [task for task in successful if "library-hidden-evaluation" in task.labels]
        evaluation_tasks = fixed + hidden
        score = (
            sum(float(task.verification.score) for task in evaluation_tasks if task.verification)
            / len(evaluation_tasks)
            if evaluation_tasks
            else 0.0
        )
        chapters = {str(card["chapter"]) for card in cards}
        required = {
            str(item) for item in domain["knowledge_selector"].get("required_capabilities", [])
        }
        present = {str(card["capability"]) for card in cards}
        critical_gaps = [
            f"Missing required capability coverage: {item}"
            for item in sorted(required)
            if not any(value.startswith(item) for value in present)
        ]
        metrics = {
            "card_count": len(cards),
            "chapter_count": len(chapters),
            "relation_coverage": round(len(related) / len(cards), 4) if cards else 0.0,
            "independent_source_ratio": round(independent / len(cards), 4) if cards else 0.0,
            "applied_task_count": len(successful),
            "fixed_test_count": len(fixed),
            "hidden_test_count": len(hidden),
            "evaluation_score": round(score, 4),
        }
        gates = {
            "knowledge_scope": (
                metrics["card_count"] >= thresholds["min_cards"]
                and metrics["chapter_count"] >= thresholds["min_chapters"]
                and not critical_gaps
            ),
            "card_structure": metrics["relation_coverage"] >= thresholds["min_relation_coverage"],
            "evidence_quality": (
                metrics["independent_source_ratio"]
                >= thresholds["min_independent_source_ratio"]
            ),
            "application": metrics["applied_task_count"] >= thresholds["min_applied_tasks"],
            "objective_evaluation": (
                metrics["fixed_test_count"] >= thresholds["min_fixed_tests"]
                and metrics["hidden_test_count"] >= thresholds["min_hidden_tests"]
                and metrics["evaluation_score"] >= thresholds["min_evaluation_score"]
            ),
        }
        source_digest = self._source_digest(cards, relations, tasks)
        passed = all(gates.values())
        report = self.store.create_readiness_report(
            {
                "domain_id": domain_id,
                "source_digest": source_digest,
                "threshold_digest": self._hash(thresholds),
                "thresholds": thresholds,
                "metrics": metrics,
                "gates": gates,
                "critical_gaps": critical_gaps,
                "passed": passed,
            }
        )
        self.store.update_library_domain(
            domain_id,
            status="author_ready" if passed else self._learning_status(metrics),
        )
        return report

    def author(self, domain_id: str, *, reason: str = "Readiness gate passed") -> dict[str, Any]:
        domain = self.store.get_library_domain(domain_id)
        report = self.store.latest_readiness_report(domain_id)
        cards = self.cards(domain_id)
        relations = self.store.list_knowledge_relations(
            [str(item["knowledge_id"]) for item in cards]
        )
        tasks = self.store.list_tasks_with_label(
            f"library-domain:{domain['slug']}", limit=5000
        )
        current_digest = self._source_digest(cards, relations, tasks)
        if not report or not report["passed"]:
            raise LibraryReadinessError("Domain has not passed the frozen readiness gates.")
        if report["source_digest"] != current_digest:
            raise LibraryReadinessError(
                "Knowledge changed after readiness evaluation; re-evaluate first."
            )
        book = self.store.create_or_get_library_book(
            {
                "domain_id": domain_id,
                "title": domain["title"],
                "description": domain["description"],
            }
        )
        markdown = self._render_book(domain, cards, report)
        content_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        duplicate = self.store.find_book_revision_by_hash(str(book["book_id"]), content_sha256)
        if duplicate:
            return {**duplicate, "created": False, "reason": "identical_content"}
        revisions = self.store.list_book_revisions(str(book["book_id"]))
        previous = revisions[0] if revisions else None
        revision_number = int(book["current_revision"]) + 1
        revision_dir = (
            self.settings.library_books_dir
            / str(book["book_id"])
            / f"r{revision_number}"
        )
        revision_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = revision_dir / "book.md"
        manifest_path = revision_dir / "manifest.json"
        self._atomic_write(markdown_path, markdown)
        citations = sorted(
            {
                str(source.get("url") or source.get("evidence_id"))
                for card in cards
                for source in card["sources"]
                if source.get("url") or source.get("evidence_id")
            }
        )
        manifest = {
            "schema_version": "eck-library-book.v1",
            "book_id": book["book_id"],
            "domain_id": domain_id,
            "revision": revision_number,
            "content_sha256": content_sha256,
            "previous_sha256": previous["content_sha256"] if previous else "",
            "readiness_report_id": report["report_id"],
            "knowledge_ids": [card["knowledge_id"] for card in cards],
            "citations": citations,
            "formal": True,
        }
        self._atomic_write(
            manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        revision = self.store.create_book_revision(
            {
                "book_id": book["book_id"],
                "readiness_report_id": report["report_id"],
                "content_sha256": content_sha256,
                "previous_sha256": previous["content_sha256"] if previous else "",
                "markdown_path": str(markdown_path.resolve()),
                "manifest_path": str(manifest_path.resolve()),
                "diff_summary": self._diff_summary(previous, markdown),
                "reason": reason,
                "citations": citations,
            }
        )
        self.store.update_library_domain(domain_id, status="published")
        return {**revision, "created": True}

    async def suggest(
        self,
        *,
        book_id: str,
        revision_id: str | None,
        suggestion_type: str,
        content: str,
    ) -> dict[str, Any]:
        book = self.store.get_library_book(book_id)
        mission = await self.missions.create(
            MissionCreate(
                title=f"Library revision: {book['title']}",
                objective=content,
                completion_requirements=(
                    "Re-check affected factual claims against authoritative Knowledge "
                    "and evidence; "
                    "create a new revision only when verification passes."
                ),
                source="human",
                schedule="manual",
                priority="normal",
                execution_kind="auto",
            )
        )
        return self.store.create_library_suggestion(
            {
                "book_id": book_id,
                "revision_id": revision_id,
                "suggestion_type": suggestion_type,
                "content": content,
                "mission_id": mission.mission_id,
            }
        )

    def book(self, book_id: str) -> dict[str, Any]:
        book = self.store.get_library_book(book_id)
        revisions = self.store.list_book_revisions(book_id)
        return {
            **book,
            "revisions": revisions,
            "suggestions": self.store.list_library_suggestions(book_id),
            "domain": self.store.get_library_domain(str(book["domain_id"])),
        }

    def _book_summary(self, book: dict[str, Any]) -> dict[str, Any]:
        revisions = self.store.list_book_revisions(str(book["book_id"]))
        return {**book, "latest_revision": revisions[0] if revisions else None}

    def _task(self, task_id: str) -> TaskRecord | None:
        try:
            return self.store.get_task(task_id)
        except KeyError:
            return None

    @staticmethod
    def _sources(task: TaskRecord | None) -> list[dict[str, Any]]:
        if not task or not task.result:
            return []
        return [
            {
                "evidence_id": item.evidence_id,
                "kind": item.source.value,
                "claim": item.claim,
                "url": LibraryAuthoringService._source_url(item.payload),
                "independence_key": LibraryAuthoringService._source_key(item.payload),
            }
            for item in task.result.evidence
        ]

    @staticmethod
    def _source_url(payload: dict[str, Any]) -> str:
        for key in ("url", "source_url", "canonical_url", "uri"):
            value = str(payload.get(key, ""))
            if value.startswith(("https://", "http://", "eck://")):
                return value
        return ""

    @staticmethod
    def _source_key(payload: dict[str, Any]) -> str:
        url = LibraryAuthoringService._source_url(payload)
        if url:
            parsed = urlparse(url)
            return parsed.hostname or parsed.scheme
        for key in ("source_domain", "provider", "publisher"):
            if payload.get(key):
                return str(payload[key]).casefold()
        return ""

    @staticmethod
    def _independent_sources(sources: list[dict[str, Any]]) -> set[str]:
        return {str(item["independence_key"]) for item in sources if item["independence_key"]}

    @staticmethod
    def _source_digest(
        cards: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        tasks: list[TaskRecord],
    ) -> str:
        return LibraryAuthoringService._hash(
            {
                "cards": cards,
                "relations": relations,
                "evaluation_tasks": [task.model_dump(mode="json") for task in tasks],
            }
        )

    @staticmethod
    def _learning_status(metrics: dict[str, Any]) -> str:
        if metrics["card_count"] == 0:
            return "exploring"
        if metrics["relation_coverage"] < 0.25:
            return "learning"
        if metrics["fixed_test_count"] == 0 or metrics["hidden_test_count"] == 0:
            return "structuring"
        return "evaluating"

    @staticmethod
    def _render_book(
        domain: dict[str, Any], cards: list[dict[str, Any]], report: dict[str, Any]
    ) -> str:
        chapters: dict[str, list[dict[str, Any]]] = {}
        for card in cards:
            chapters.setdefault(str(card["chapter"]), []).append(card)
        lines = [
            f"# {domain['title']}",
            "",
            str(domain["description"]),
            "",
            f"> Readiness report: `{report['report_id']}`",
            f"> Source digest: `{report['source_digest']}`",
            "",
        ]
        for chapter, items in sorted(chapters.items()):
            lines.extend((f"## {chapter}", ""))
            for card in items:
                lines.extend(
                    (
                        f"### {card['title']}",
                        "",
                        str(card["core_claim"]),
                        "",
                        str(card["explanation"]),
                        "",
                        f"- Confidence: {card['confidence']}",
                        f"- Applicability: {card['applicability']}",
                        f"- Verification: {card['verification_status']}",
                    )
                )
                for source in card["sources"]:
                    suffix = f" ({source['url']})" if source["url"] else ""
                    lines.append(f"- Source: {source['claim']}{suffix}")
                for usage in card["skill_usages"]:
                    lines.append(
                        "- Executed skill: "
                        f"{usage['skill_name']} v{usage['skill_version']} "
                        f"({usage['verification_status']})"
                    )
                for counterexample in card["counterexamples"]:
                    lines.append(f"- Counterexample / failed check: {counterexample}")
                for question in card["unresolved_questions"]:
                    lines.append(f"- Unresolved question: {question}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _diff_summary(self, previous: dict[str, Any] | None, markdown: str) -> str:
        if previous is None:
            return "Initial verified publication."
        prior_path = Path(str(previous["markdown_path"]))
        prior = prior_path.read_text(encoding="utf-8") if prior_path.exists() else ""
        diff = list(difflib.unified_diff(prior.splitlines(), markdown.splitlines()))
        additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removals = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        return f"{additions} added lines; {removals} removed lines."

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return slug or "domain-" + hashlib.sha256(value.encode()).hexdigest()[:8]

    @staticmethod
    def _chapter(capability: str) -> str:
        return capability.replace("_", " ").replace(".", " / ").title()

    @staticmethod
    def _title(value: str) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= 96 else normalized[:93] + "..."

    @staticmethod
    def _hash(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _validate_thresholds(thresholds: dict[str, Any]) -> None:
        required = {
            "min_cards",
            "min_chapters",
            "min_relation_coverage",
            "min_independent_source_ratio",
            "min_applied_tasks",
            "min_fixed_tests",
            "min_hidden_tests",
            "min_evaluation_score",
        }
        unknown = set(thresholds) - required
        if unknown:
            raise ValueError(f"Unsupported Library thresholds: {sorted(unknown)}")
        for key in required:
            value = thresholds.get(key)
            if not isinstance(value, int | float):
                raise ValueError(f"Library threshold must be numeric: {key}")
            if value < 0:
                raise ValueError(f"Library threshold cannot be negative: {key}")
        for key in (
            "min_relation_coverage",
            "min_independent_source_ratio",
            "min_evaluation_score",
        ):
            if float(thresholds[key]) > 1:
                raise ValueError(f"Library ratio threshold cannot exceed 1: {key}")
