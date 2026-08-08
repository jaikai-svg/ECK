from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

from eck.domain.enums import RuntimeSkillStatus
from eck.storage.sqlite import SQLiteStore

_CATEGORY_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("AI", "影片生成"), ("video", "影片", "cogvideo", "framepack", "wan", "ltx")),
    (("AI", "圖片生成"), ("image", "圖片", "圖像", "stable diffusion", "rembg")),
    (("AI", "Agent"), ("agent", "agi", "eck", "multi-agent", "workflow", "llm")),
    (("自動化", "網頁操作"), ("browser", "web", "網頁", "playwright", "crawl")),
    (("自動化", "社群平台"), ("social", "社群", "publish", "reply", "follow")),
    (("軟體工程", "程式與測試"), ("code", "git", "python", "node", "test", "debug")),
    (("知識研究", "學術與論文"), ("paper", "academic", "論文", "學術", "science")),
    (("知識研究", "商業與經濟"), ("business", "finance", "econom", "企業", "金融", "經濟")),
    (("資料能力", "分析"), ("data", "analysis", "資料", "統計")),
    (("內容製作", "文件"), ("document", "pdf", "docx", "pptx", "xlsx", "文件")),
)

_OFFICIAL_SOURCES: dict[str, tuple[dict[str, Any], ...]] = {
    "video.generate:cogvideox-2b:v1": (
        {
            "title": "CogVideo official repository",
            "url": "https://github.com/zai-org/CogVideo",
            "source_type": "repository",
            "verified": True,
        },
        {
            "title": "CogVideoX-2B model weights",
            "url": "https://huggingface.co/zai-org/CogVideoX-2b",
            "source_type": "model",
            "verified": True,
        },
    ),
}


class SkillKnowledgeGraphService:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        capability_provider: Callable[[], Iterable[dict[str, Any]]] | None = None,
        cache_seconds: float = 30,
    ) -> None:
        self.store = store
        self.capability_provider = capability_provider
        self.cache_seconds = cache_seconds
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0

    def invalidate(self) -> None:
        self._cache = None
        self._cached_at = 0.0

    def build(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._cache is not None
            and now - self._cached_at < self.cache_seconds
        ):
            return self._cache

        root = self._node("memory", "ECK 記憶", "root", "active")
        category_nodes: dict[tuple[str, ...], dict[str, Any]] = {(): root}
        flat_items: list[dict[str, Any]] = []

        research_runs = self.store.list_research_runs(limit=200)
        research_sources = self._all_research_sources(research_runs)
        known_fingerprints: set[str] = set()
        for learned_skill in self.store.list_skills(limit=10000):
            known_fingerprints.add(learned_skill.fingerprint)
            path = self._classify(
                " ".join(
                    (
                        learned_skill.name,
                        learned_skill.capability,
                        learned_skill.fingerprint,
                    )
                )
            )
            sources = list(_OFFICIAL_SOURCES.get(learned_skill.fingerprint, ()))
            if learned_skill.capability == "web.critical_research":
                sources.extend(research_sources[:30])
            sources.extend(
                {
                    "title": f"驗證證據 {evidence_id}",
                    "reference": evidence_id,
                    "source_type": "verification",
                    "verified": True,
                }
                for evidence_id in self._verification_evidence(
                    learned_skill.verification_basis
                )
            )
            item = self._node(
                f"skill:{learned_skill.skill_id}",
                learned_skill.name,
                "skill",
                "acquired" if learned_skill.active else "learning",
                gold=learned_skill.active,
                path=list(path),
                capability=learned_skill.capability,
                fingerprint=learned_skill.fingerprint,
                success_count=learned_skill.success_count,
                failure_count=learned_skill.failure_count,
                procedure=learned_skill.procedure,
                sources=self._dedupe_sources(sources),
                created_at=learned_skill.created_at.isoformat(),
                updated_at=learned_skill.updated_at.isoformat(),
            )
            self._attach(root, category_nodes, path, item)
            flat_items.append(item)

        if self.capability_provider is not None:
            for capability in self.capability_provider():
                fingerprint = str(capability.get("fingerprint", "")).strip()
                if not fingerprint or fingerprint in known_fingerprints:
                    continue
                path = self._classify(
                    " ".join(
                        (
                            str(capability.get("title", "")),
                            str(capability.get("capability", "")),
                            fingerprint,
                        )
                    )
                )
                acquired = bool(capability.get("acquired"))
                sources = list(_OFFICIAL_SOURCES.get(fingerprint, ()))
                sources.extend(capability.get("sources", []))
                item = self._node(
                    f"capability:{fingerprint}",
                    str(capability.get("title") or fingerprint),
                    "capability",
                    "acquired" if acquired else "learning",
                    gold=acquired,
                    path=list(path),
                    capability=capability.get("capability"),
                    fingerprint=fingerprint,
                    description=capability.get("description", ""),
                    procedure=capability.get("procedure", {}),
                    sources=self._dedupe_sources(sources),
                    verification=capability.get("verification", {}),
                    runtime_available=bool(capability.get("runtime_available")),
                )
                self._attach(root, category_nodes, path, item)
                flat_items.append(item)

        for runtime_skill in self.store.list_runtime_skills(limit=10000):
            text = " ".join(
                (
                    runtime_skill.manifest.name,
                    runtime_skill.manifest.category,
                    runtime_skill.manifest.description,
                )
            )
            path = self._classify(text)
            active = runtime_skill.status is RuntimeSkillStatus.ACTIVE
            source = {
                "title": (
                    f"技能程式：{runtime_skill.manifest.name} "
                    f"{runtime_skill.manifest.version}"
                ),
                "reference": runtime_skill.source_dir,
                "source_type": "code",
                "verified": active,
            }
            item = self._node(
                f"runtime-skill:{runtime_skill.runtime_skill_id}",
                runtime_skill.manifest.name,
                "runtime_skill",
                "acquired" if active else runtime_skill.status.value,
                gold=active,
                path=list(path),
                description=runtime_skill.manifest.description,
                version=runtime_skill.manifest.version,
                operations=list(runtime_skill.manifest.operations),
                permissions=list(runtime_skill.manifest.permissions),
                improvements=list(runtime_skill.improvements),
                activation_count=runtime_skill.activation_count,
                sources=[source],
                test_report=runtime_skill.test_report,
                created_at=runtime_skill.created_at.isoformat(),
                updated_at=runtime_skill.updated_at.isoformat(),
            )
            self._attach(root, category_nodes, path, item)
            flat_items.append(item)

        for run in research_runs:
            if run.get("status") != "completed":
                continue
            topic = str(run.get("topic", "未命名研究"))
            path = self._classify(topic)
            conclusion_status = str(run.get("conclusion_status", "unverified"))
            sources = [self._source_from_snapshot(source) for source in run.get("sources", [])]
            item = self._node(
                f"research:{run['run_id']}",
                topic,
                "knowledge",
                conclusion_status,
                gold=False,
                path=list(path),
                conclusion=run.get("conclusion", ""),
                confidence=run.get("confidence", 0),
                claims=run.get("claims", []),
                sources=self._dedupe_sources(sources),
                started_at=run.get("started_at"),
                finished_at=run.get("finished_at"),
            )
            self._attach(root, category_nodes, path, item)
            flat_items.append(item)

        graph = {
            "schema_version": "eck-skill-knowledge-graph.v1",
            "portable": True,
                "derived_from": [
                    "skills",
                    "runtime_skills",
                    "research_runs",
                    "research_source_snapshots",
                    "verified_capability_status",
                ],
            "tree": root,
            "items": flat_items,
            "stats": {
                "acquired_skills": sum(
                    item["type"] in {"skill", "runtime_skill", "capability"}
                    and item["gold"]
                    for item in flat_items
                ),
                "learning_skills": sum(
                    item["type"] in {"skill", "runtime_skill", "capability"}
                    and not item["gold"]
                    for item in flat_items
                ),
                "research_results": sum(item["type"] == "knowledge" for item in flat_items),
                "traceable_sources": len(
                    {
                        source.get("url") or source.get("reference")
                        for item in flat_items
                        for source in item.get("sources", [])
                        if source.get("url") or source.get("reference")
                    }
                ),
            },
        }
        self._cache = graph
        self._cached_at = now
        return graph

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        terms = self._terms(query)
        if not terms:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self.build()["items"]:
            haystack = " ".join(
                (
                    str(item.get("title", "")),
                    str(item.get("capability", "")),
                    " ".join(item.get("path", [])),
                    str(item.get("description", "")),
                    str(item.get("conclusion", "")),
                )
            ).lower()
            title = str(item.get("title", "")).lower()
            score = sum(
                3 if term in title else 1
                for term in terms
                if term in haystack
            )
            if score:
                scored.append((score + (2 if item.get("gold") else 0), item))
        scored.sort(key=lambda pair: (pair[0], str(pair[1].get("updated_at", ""))), reverse=True)
        return [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "title",
                    "type",
                    "status",
                    "gold",
                    "path",
                    "capability",
                    "procedure",
                    "operations",
                    "improvements",
                    "conclusion",
                    "sources",
                )
                if item.get(key) not in (None, [], {})
            }
            for _, item in scored[:limit]
        ]

    @classmethod
    def _classify(cls, text: str) -> tuple[str, ...]:
        normalized = text.lower()
        for path, keywords in _CATEGORY_RULES:
            if any(keyword in normalized for keyword in keywords):
                return path
        return ("其他能力", "未分類")

    @classmethod
    def _attach(
        cls,
        root: dict[str, Any],
        category_nodes: dict[tuple[str, ...], dict[str, Any]],
        path: tuple[str, ...],
        item: dict[str, Any],
    ) -> None:
        parent = root
        for depth in range(1, len(path) + 1):
            current_path = path[:depth]
            category = category_nodes.get(current_path)
            if category is None:
                category = cls._node(
                    f"category:{'/'.join(current_path)}",
                    current_path[-1],
                    "category",
                    "active",
                    path=list(current_path),
                )
                category_nodes[current_path] = category
                parent["children"].append(category)
            parent = category
        parent["children"].append(item)

    @staticmethod
    def _node(
        identity: str,
        title: str,
        node_type: str,
        status: str,
        **details: Any,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return {
            "id": f"graph-{digest}",
            "title": title,
            "type": node_type,
            "status": status,
            "gold": bool(details.pop("gold", False)),
            "children": [],
            **details,
        }

    @staticmethod
    def _verification_evidence(basis: dict[str, Any]) -> Iterable[str]:
        values = basis.get("evidence_ids", [])
        if not isinstance(values, list):
            return ()
        return (str(value) for value in values if str(value).strip())

    @classmethod
    def _all_research_sources(cls, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return cls._dedupe_sources(
            cls._source_from_snapshot(source)
            for run in runs
            if run.get("status") == "completed"
            for source in run.get("sources", [])
            if isinstance(source, dict)
        )

    @staticmethod
    def _source_from_snapshot(source: dict[str, Any]) -> dict[str, Any]:
        url = str(source.get("canonical_url", ""))
        domain = str(source.get("source_domain", "")).lower()
        if "youtube.com" in domain or "youtu.be" in domain:
            source_type = "video"
        elif "github.com" in domain:
            source_type = "repository"
        elif "arxiv.org" in domain or "doi.org" in domain:
            source_type = "paper"
        elif any(name in domain for name in ("reddit.com", "news.ycombinator.com")):
            source_type = "social"
        else:
            source_type = "article"
        return {
            "title": source.get("title") or domain or url,
            "url": url,
            "source_type": source_type,
            "provider": source.get("provider"),
            "published_at": source.get("published_at"),
            "fetched_at": source.get("fetched_at"),
            "snapshot_id": source.get("snapshot_id"),
            "verified": True,
        }

    @staticmethod
    def _dedupe_sources(sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in sources:
            identity = str(source.get("url") or source.get("reference") or source.get("title"))
            if not identity or identity in seen:
                continue
            seen.add(identity)
            deduped.append(source)
        return deduped

    @staticmethod
    def _terms(query: str) -> tuple[str, ...]:
        return tuple(
            term.lower()
            for term in re.findall(r"[\w\u4e00-\u9fff-]{2,}", query)
            if len(term) >= 2
        )
