from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from eck.config import Settings
from eck.core.time import iso_now
from eck.domain.models import KnowledgeRecord, ReflectionRecord, TaskRecord
from eck.modules.library.quality import normalize_claim, unique_text, unresolved_questions


class LibraryRepository(Protocol):
    def list_knowledge(self, limit: int = 100) -> list[KnowledgeRecord]: ...

    def list_reflections(self, limit: int = 100) -> list[ReflectionRecord]: ...

    def get_task(self, task_id: str) -> TaskRecord: ...


class LibraryProjectionService:
    """Rebuildable Markdown/JSON projection over verified ECK knowledge."""

    schema_version = "eck-library.v1"
    projection_revision = 2

    def __init__(self, settings: Settings, repository: LibraryRepository) -> None:
        self.repository = repository
        self.cache_dir = settings.data_dir.resolve() / "library-cache"
        self.catalog_path = self.cache_dir / "catalog.json"
        self.book_path = self.cache_dir / "eck-verified-knowledge.md"

    def page(
        self,
        *,
        limit: int,
        offset: int,
        query: str = "",
    ) -> dict[str, Any]:
        catalog, cache_hit = self._catalog()
        items = list(catalog.get("cards", []))
        normalized = query.casefold().strip()
        if normalized:
            items = [
                item
                for item in items
                if normalized
                in " ".join(
                    (
                        str(item.get("title", "")),
                        str(item.get("claim", "")),
                        str(item.get("capability", "")),
                    )
                ).casefold()
            ]
        selected = items[offset : offset + limit]
        next_offset = offset + len(selected)
        return {
            "schema_version": self.schema_version,
            "source_authority": "knowledge_items + tasks + reflections",
            "cache": {
                "hit": cache_hit,
                "format": "Markdown/JSON",
                "content_sha256": catalog.get("content_sha256", ""),
                "incremental": True,
                "rebuildable": True,
            },
            "book": catalog.get("book", {}),
            "chapters": catalog.get("chapters", []),
            "items": selected,
            "page": {
                "limit": limit,
                "offset": offset,
                "total": len(items),
                "next_offset": next_offset if next_offset < len(items) else None,
            },
        }

    def status(self) -> dict[str, Any]:
        catalog, cache_hit = self._catalog()
        return {
            "schema_version": self.schema_version,
            "cards": len(catalog.get("cards", [])),
            "chapters": len(catalog.get("chapters", [])),
            "book": catalog.get("book", {}),
            "cache_hit": cache_hit,
            "content_sha256": catalog.get("content_sha256", ""),
        }

    def _catalog(self) -> tuple[dict[str, Any], bool]:
        knowledge = self.repository.list_knowledge(limit=2000)
        reflections = self.repository.list_reflections(limit=2000)
        source_digest = self._source_digest(knowledge, reflections)
        previous = self._read_catalog()
        if (
            previous.get("source_digest") == source_digest
            and previous.get("projection_revision") == self.projection_revision
        ):
            return previous, True
        catalog = self._build_catalog(
            knowledge,
            reflections,
            source_digest=source_digest,
            previous=previous,
        )
        self._write_cache(catalog)
        return catalog, False

    def _build_catalog(
        self,
        knowledge: list[KnowledgeRecord],
        reflections: list[ReflectionRecord],
        *,
        source_digest: str,
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        reflection_by_task = {item.task_id: item for item in reflections}
        previous_cards: dict[str, dict[str, Any]] = {}
        for item in previous.get("cards", []):
            if not isinstance(item, dict):
                continue
            member_ids = item.get("knowledge_ids") or [item.get("knowledge_id")]
            for knowledge_id in member_ids:
                if knowledge_id:
                    previous_cards[str(knowledge_id)] = item
        grouped: dict[str, list[KnowledgeRecord]] = {}
        for item in knowledge:
            if item.admitted:
                grouped.setdefault(normalize_claim(item.claim), []).append(item)
        cards = []
        for members in grouped.values():
            members.sort(key=lambda item: item.created_at)
            previous_card = next(
                (
                    previous_cards[item.knowledge_id]
                    for item in members
                    if item.knowledge_id in previous_cards
                ),
                None,
            )
            canonical_id = str(previous_card.get("knowledge_id", "")) if previous_card else ""
            canonical = next(
                (item for item in members if item.knowledge_id == canonical_id),
                members[0],
            )
            ordered = [canonical, *(item for item in reversed(members) if item != canonical)]
            cards.append(self._card(ordered, reflection_by_task, previous_card))
        cards.sort(
            key=lambda item: str(item.get("latest_occurrence_at", "")),
            reverse=True,
        )
        chapter_map: dict[str, list[dict[str, Any]]] = {}
        for card in cards:
            chapter_map.setdefault(str(card["chapter"]), []).append(card)
        chapters = [
            {
                "chapter_id": self._slug(title),
                "title": title,
                "card_count": len(items),
                "card_ids": [str(item["knowledge_id"]) for item in items],
                "content_sha256": self._hash(items),
            }
            for title, items in sorted(chapter_map.items())
        ]
        prior_book = previous.get("book", {})
        revision = int(prior_book.get("revision", 0)) + 1
        book = {
            "book_id": "eck-verified-knowledge",
            "title": "ECK Verified Knowledge Catalog",
            "description": "由已准入知識與可追溯驗證紀錄增量產生。",
            "revision": revision,
            "card_count": len(cards),
            "chapter_count": len(chapters),
            "generated_at": iso_now(),
            "unresolved_question_count": sum(
                len(item.get("unresolved_questions", [])) for item in cards
            ),
            "counterexample_count": sum(len(item.get("counterexamples", [])) for item in cards),
            "formal": False,
            "status": "knowledge_catalog",
            "publication_blocked": True,
            "publication_reason": "Formal books require a passed domain readiness report.",
        }
        content = {
            "schema_version": self.schema_version,
            "projection_revision": self.projection_revision,
            "source_digest": source_digest,
            "book": book,
            "chapters": chapters,
            "cards": cards,
        }
        content["content_sha256"] = self._hash(content)
        return content

    def _card(
        self,
        knowledge_group: list[KnowledgeRecord],
        reflection_by_task: dict[str, ReflectionRecord],
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        knowledge = knowledge_group[0]
        sources: list[dict[str, Any]] = []
        counterexamples: list[str] = []
        confidence_scores: list[float] = []
        verification_results: list[str] = []
        open_questions: list[str] = []
        source_evidence_ids: list[str] = []
        for item in knowledge_group:
            source_evidence_ids.extend(item.evidence_ids)
            item_reflection = reflection_by_task.get(item.task_id)
            open_questions.extend(
                unresolved_questions(
                    item_reflection.next_step if item_reflection is not None else ""
                )
            )
            try:
                task = self.repository.get_task(item.task_id)
            except KeyError:
                task = None
            if task is None:
                confidence_scores.append(
                    1.0 if item.externally_grounded and item.reproducible else 0.0
                )
                verification_results.append(item.outcome.value)
                continue
            if task.result:
                sources.extend(
                    {
                        "evidence_id": evidence.evidence_id,
                        "kind": evidence.source.value,
                        "claim": evidence.claim,
                        "url": self._source_url(evidence.payload),
                    }
                    for evidence in task.result.evidence
                )
            if task.verification:
                confidence_scores.append(float(task.verification.score))
                verification_results.append(task.verification.status.value)
                counterexamples.extend(task.verification.failed_checks)
                counterexamples.extend(task.verification.violated_constraints)
            else:
                confidence_scores.append(0.0)
                verification_results.append(item.outcome.value)
        unique_sources = {
            str(item.get("evidence_id") or self._hash(item)): item for item in sources
        }
        latest = max(knowledge_group, key=lambda item: item.created_at)
        reflection = reflection_by_task.get(latest.task_id)
        verification_result = (
            verification_results[0]
            if len(set(verification_results)) == 1
            else "mixed"
        )
        chapter = self._chapter_title(knowledge.capability)
        base = {
            "knowledge_id": knowledge.knowledge_id,
            "task_id": knowledge.task_id,
            "knowledge_ids": [item.knowledge_id for item in knowledge_group],
            "task_ids": [item.task_id for item in knowledge_group],
            "occurrence_count": len(knowledge_group),
            "title": self._title(knowledge.claim),
            "claim": knowledge.claim,
            "capability": knowledge.capability,
            "chapter": chapter,
            "sources": list(unique_sources.values()),
            "source_evidence_ids": unique_text(source_evidence_ids),
            "counterexamples": unique_text(counterexamples),
            "confidence": round(min(confidence_scores, default=0.0), 4),
            "confidence_basis": "contract_verification_score",
            "externally_grounded": all(
                item.externally_grounded for item in knowledge_group
            ),
            "reproducible": all(item.reproducible for item in knowledge_group),
            "verification_result": verification_result,
            "unresolved_questions": unique_text(open_questions),
            "reflection": {
                "observation": reflection.observation if reflection else "",
                "lesson": reflection.lesson if reflection else "",
            },
            "created_at": knowledge.created_at.isoformat(),
            "latest_occurrence_at": latest.created_at.isoformat(),
        }
        content_sha256 = self._hash(base)
        history = list(previous.get("revision_history", [])) if previous else []
        previous_hash = str(previous.get("content_sha256", "")) if previous else ""
        if not history:
            history.append(
                {
                    "revision": 1,
                    "content_sha256": content_sha256,
                    "changed_at": knowledge.created_at.isoformat(),
                }
            )
        elif previous_hash and previous_hash != content_sha256:
            history.append(
                {
                    "revision": len(history) + 1,
                    "content_sha256": content_sha256,
                    "changed_at": iso_now(),
                }
            )
        return {
            **base,
            "content_sha256": content_sha256,
            "revision": len(history),
            "revision_history": history[-20:],
        }

    def _write_cache(self, catalog: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        catalog_text = json.dumps(catalog, ensure_ascii=False, indent=2)
        markdown = self._markdown(catalog)
        self._atomic_write(self.catalog_path, catalog_text)
        self._atomic_write(self.book_path, markdown)

    def _read_catalog(self) -> dict[str, Any]:
        try:
            value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _markdown(catalog: dict[str, Any]) -> str:
        lines = [
            f"# {catalog['book']['title']}",
            "",
            catalog["book"]["description"],
            "",
        ]
        for chapter in catalog.get("chapters", []):
            lines.extend((f"## {chapter['title']}", ""))
            chapter_id = str(chapter["chapter_id"])
            for card in catalog.get("cards", []):
                if LibraryProjectionService._slug(str(card["chapter"])) != chapter_id:
                    continue
                lines.extend(
                    (
                        f"### {card['title']}",
                        "",
                        str(card["claim"]),
                        "",
                        f"- Confidence: {card['confidence']}",
                        f"- Verification: {card['verification_result']}",
                        f"- Content SHA-256: `{card['content_sha256']}`",
                        "",
                    )
                )
                for source in card.get("sources", []):
                    url = str(source.get("url", ""))
                    suffix = f" — {url}" if url else ""
                    lines.append(f"- Source: {source.get('claim', '')}{suffix}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _source_digest(
        knowledge: list[KnowledgeRecord],
        reflections: list[ReflectionRecord],
    ) -> str:
        value = {
            "knowledge": [item.model_dump(mode="json") for item in knowledge],
            "reflections": [item.model_dump(mode="json") for item in reflections],
        }
        return LibraryProjectionService._hash(value)

    @staticmethod
    def _source_url(payload: dict[str, Any]) -> str:
        for key in ("url", "source_url", "canonical_url", "uri"):
            value = str(payload.get(key, ""))
            if value.startswith(("http://", "https://", "eck://")):
                return value
        return ""

    @staticmethod
    def _title(claim: str) -> str:
        normalized = " ".join(claim.split())
        return normalized if len(normalized) <= 86 else normalized[:83] + "…"

    @staticmethod
    def _chapter_title(capability: str) -> str:
        root = capability.split(".", 1)[0].replace("_", " ").strip()
        return root.title() if root else "General"

    @staticmethod
    def _slug(value: str) -> str:
        safe = "-".join(value.casefold().replace("_", " ").split())
        return safe or "general"

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
