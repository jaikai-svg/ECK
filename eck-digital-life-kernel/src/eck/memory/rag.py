from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from eck.config import Settings
from eck.memory.rag_runtime import EmbeddingRerankerRuntime, LocalBgeRuntime
from eck.memory.rag_store import PortableVectorStore
from eck.storage.sqlite import SQLiteStore


class PortableRagService:
    """Verified-memory retrieval with sqlite-vec coarse search and BGE reranking."""

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        *,
        runtime: EmbeddingRerankerRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        assert settings.rag_database_path is not None
        self.runtime = runtime or LocalBgeRuntime(
            settings.rag_model_dir,
            embedding_model=settings.rag_embedding_model,
            reranker_model=settings.rag_reranker_model,
            device=settings.rag_device,
            allow_download=(
                settings.rag_auto_download and settings.environment != "test"
            ),
        )
        self.vectors = PortableVectorStore(
            settings.rag_database_path,
            dimension=self.runtime.dimension,
        )
        self._lock = asyncio.Lock()
        self._initialized = False
        self._last_detail = "RAG has not been queried yet."
        self._last_indexed = 0
        self._last_candidates = 0

    async def retrieve(self, query: str) -> dict[str, Any]:
        if not self.settings.rag_enabled or len(query.strip()) < 2:
            return self._empty("Portable RAG is disabled or the query is empty.")
        runtime_status = self.runtime.status()
        if not runtime_status.get("available"):
            return self._empty("Local BGE dependencies are unavailable.")
        if self.settings.environment == "test" and not runtime_status.get("models_verified"):
            return self._empty("Tests do not download local BGE model weights.")
        async with self._lock:
            try:
                return await asyncio.to_thread(self._retrieve_sync, query.strip())
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self._last_detail = f"{type(exc).__name__}: {exc}"
                return self._empty(self._last_detail)

    def status(self) -> dict[str, Any]:
        database_exists = self.vectors.path.is_file()
        count = 0
        integrity: dict[str, Any] | None = None
        if database_exists:
            try:
                self._ensure_initialized()
                count = self.vectors.count()
                integrity = self.vectors.integrity()
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self._last_detail = f"{type(exc).__name__}: {exc}"
        return {
            "enabled": self.settings.rag_enabled,
            "available": bool(self.runtime.status().get("available")),
            "database_path": str(self.vectors.path),
            "database_exists": database_exists,
            "indexed_documents": count,
            "coarse_candidate_limit": self.settings.rag_candidate_limit,
            "final_context_limit": self.settings.rag_result_limit,
            "storage": "sqlite-vec",
            "runtime": self.runtime.status(),
            "integrity": integrity,
            "last_indexed": self._last_indexed,
            "last_candidates": self._last_candidates,
            "detail": self._last_detail,
        }

    def close(self) -> None:
        self.runtime.close()

    def _retrieve_sync(self, query: str) -> dict[str, Any]:
        self._ensure_initialized()
        documents = self._verified_documents()
        existing_hashes = self.vectors.hashes()
        pending = [
            document
            for document in documents
            if existing_hashes.get(document["document_id"]) != document["content_sha256"]
        ]
        if pending:
            embeddings = self.runtime.embed([document["content"] for document in pending])
            for document, embedding in zip(pending, embeddings, strict=True):
                self.vectors.upsert(document, embedding)
        self._last_indexed = len(pending)
        query_embedding = self.runtime.embed([query])[0]
        candidates = self.vectors.search(
            query_embedding,
            limit=self.settings.rag_candidate_limit,
        )
        self._last_candidates = len(candidates)
        scores = self.runtime.rerank(query, [item["content"] for item in candidates])
        ranked = sorted(
            (
                {
                    **candidate,
                    "reranker_score": score,
                }
                for candidate, score in zip(candidates, scores, strict=True)
            ),
            key=lambda item: item["reranker_score"],
            reverse=True,
        )[: self.settings.rag_result_limit]
        self._last_detail = (
            f"Indexed {len(pending)} changed records; reranked {len(candidates)} candidates."
        )
        return {
            "available": True,
            "items": ranked,
            "indexed": len(pending),
            "coarse_candidates": len(candidates),
            "embedding_model": self.settings.rag_embedding_model,
            "reranker_model": self.settings.rag_reranker_model,
            "detail": self._last_detail,
        }

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.vectors.initialize()
            self._initialized = True

    def _verified_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for item in self.store.list_knowledge(limit=self.settings.rag_max_source_records):
            if not (item.admitted and item.externally_grounded and item.reproducible):
                continue
            documents.append(
                self._document(
                    document_id=f"knowledge:{item.knowledge_id}",
                    source_type="verified_knowledge",
                    title=item.capability,
                    content=item.claim,
                    source_uri=f"eck://knowledge/{item.knowledge_id}",
                    metadata={
                        "task_id": item.task_id,
                        "outcome": item.outcome.value,
                        "evidence_ids": list(item.evidence_ids),
                    },
                    updated_at=item.created_at.isoformat(),
                )
            )
        research_limit = min(1000, self.settings.rag_max_source_records)
        for run in self.store.list_research_runs(limit=research_limit):
            if run.get("status") != "completed" or run.get("conclusion_status") not in {
                "supported",
                "partially_supported",
            }:
                continue
            claims = [
                str(item.get("claim", ""))
                for item in run.get("claims", [])
                if isinstance(item, dict) and item.get("status") == "supported"
            ]
            content = "\n".join(
                value
                for value in [
                    str(run.get("topic", "")),
                    str(run.get("conclusion", "")),
                    *claims,
                ]
                if value.strip()
            )[:12000]
            if not content:
                continue
            sources = [
                item
                for item in run.get("sources", [])
                if isinstance(item, dict) and item.get("canonical_url")
            ]
            source_uri = (
                str(sources[0]["canonical_url"])
                if sources
                else f"eck://research/{run['run_id']}"
            )
            documents.append(
                self._document(
                    document_id=f"research:{run['run_id']}",
                    source_type="verified_research",
                    title=str(run.get("topic", "Verified research")),
                    content=content,
                    source_uri=source_uri,
                    metadata={
                        "run_id": run["run_id"],
                        "conclusion_status": run.get("conclusion_status"),
                        "confidence": run.get("confidence"),
                        "sources": [
                            {
                                "title": item.get("title"),
                                "url": item.get("canonical_url"),
                                "content_sha256": item.get("content_sha256"),
                            }
                            for item in sources[:8]
                        ],
                    },
                    updated_at=str(run.get("finished_at") or run.get("started_at") or ""),
                )
            )
        return documents

    @staticmethod
    def _document(
        *,
        document_id: str,
        source_type: str,
        title: str,
        content: str,
        source_uri: str,
        metadata: dict[str, Any],
        updated_at: str,
    ) -> dict[str, Any]:
        canonical = json.dumps(
            {
                "source_type": source_type,
                "title": title,
                "content": content,
                "source_uri": source_uri,
                "metadata": metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "document_id": document_id,
            "source_type": source_type,
            "title": title[:500],
            "content": content[:12000],
            "source_uri": source_uri[:2000],
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "metadata": metadata,
            "updated_at": updated_at or datetime.now(UTC).isoformat(),
        }

    def _empty(self, detail: str) -> dict[str, Any]:
        return {
            "available": False,
            "items": [],
            "indexed": 0,
            "coarse_candidates": 0,
            "embedding_model": self.settings.rag_embedding_model,
            "reranker_model": self.settings.rag_reranker_model,
            "detail": detail,
        }
