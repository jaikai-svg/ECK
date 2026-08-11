from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import threading
from pathlib import Path
from typing import Any, Protocol


class EmbeddingRerankerRuntime(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def rerank(self, query: str, documents: list[str]) -> list[float]: ...

    def status(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class LocalBgeRuntime:
    """Lazy local BGE embedding and cross-encoder runtime."""

    dimension = 1024

    def __init__(
        self,
        model_dir: Path,
        *,
        embedding_model: str,
        reranker_model: str,
        device: str,
        allow_download: bool,
    ) -> None:
        self.model_dir = model_dir.resolve()
        self.embedding_model = embedding_model
        self.reranker_model = reranker_model
        self.device = device
        self.allow_download = allow_download
        self.manifest_path = self.model_dir / "eck-rag-models.json"
        self._embedder: Any | None = None
        self._reranker: Any | None = None
        self._lock = threading.RLock()
        self._detail = "Models load on the first retrieval request."

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            model = self._ensure_embedder()
            vectors = model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
                batch_size=min(16, len(texts)),
            )
            result = [[float(value) for value in row] for row in vectors.tolist()]
            if any(len(row) != self.dimension for row in result):
                raise RuntimeError("BGE-M3 returned an unexpected embedding dimension.")
            return result

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        with self._lock:
            model = self._ensure_reranker()
            scores = model.predict(
                [[query, document] for document in documents],
                batch_size=min(8, len(documents)),
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            flattened = scores.reshape(-1).tolist()
            return [float(value) for value in flattened]

    def status(self) -> dict[str, Any]:
        dependencies = {
            "sentence_transformers": importlib.util.find_spec("sentence_transformers")
            is not None,
            "torch": importlib.util.find_spec("torch") is not None,
        }
        manifest = self._manifest()
        return {
            "available": all(dependencies.values()),
            "dependencies": dependencies,
            "embedding_model": self.embedding_model,
            "reranker_model": self.reranker_model,
            "dimension": self.dimension,
            "device": self.device,
            "allow_download": self.allow_download,
            "models_verified": bool(manifest.get("verified")),
            "models_warm": self._embedder is not None or self._reranker is not None,
            "model_dir": str(self.model_dir),
            "detail": self._detail,
        }

    def close(self) -> None:
        with self._lock:
            self._embedder = None
            self._reranker = None
            gc.collect()

    def _ensure_embedder(self) -> Any:
        if self._embedder is None:
            sentence_transformers = self._module()
            self.model_dir.mkdir(parents=True, exist_ok=True)
            self._embedder = sentence_transformers.SentenceTransformer(
                self.embedding_model,
                device=self.device,
                cache_folder=str(self.model_dir),
                local_files_only=self._local_files_only("embedding_ready"),
                trust_remote_code=False,
            )
            self._detail = "BGE-M3 embedding model is warm."
            self._write_manifest(embedding_ready=True)
        return self._embedder

    def _ensure_reranker(self) -> Any:
        if self._reranker is None:
            sentence_transformers = self._module()
            self.model_dir.mkdir(parents=True, exist_ok=True)
            self._reranker = sentence_transformers.CrossEncoder(
                self.reranker_model,
                device=self.device,
                cache_folder=str(self.model_dir),
                local_files_only=self._local_files_only("reranker_ready"),
                trust_remote_code=False,
                max_length=512,
            )
            self._detail = "BGE embedding and bge-reranker-large are warm."
            self._write_manifest(reranker_ready=True)
        return self._reranker

    @staticmethod
    def _module() -> Any:
        try:
            return importlib.import_module("sentence_transformers")
        except ImportError as exc:
            raise RuntimeError(
                "Local BGE dependencies are missing; install the 'rag' optional dependency."
            ) from exc

    def _manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return {}
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _local_files_only(self, readiness_key: str) -> bool:
        return not self.allow_download or bool(self._manifest().get(readiness_key))

    def _write_manifest(
        self,
        *,
        embedding_ready: bool = False,
        reranker_ready: bool = False,
    ) -> None:
        manifest = self._manifest()
        embedding_verified = bool(manifest.get("embedding_ready")) or embedding_ready
        reranker_verified = bool(manifest.get("reranker_ready")) or reranker_ready
        payload = {
            "schema": "eck-local-rag-models.v1",
            "embedding_model": self.embedding_model,
            "reranker_model": self.reranker_model,
            "dimension": self.dimension,
            "device": self.device,
            "embedding_ready": embedding_verified,
            "reranker_ready": reranker_verified,
            "verified": embedding_verified and reranker_verified,
        }
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
