from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from eck.config import Settings
from eck.memory.rag_runtime import LocalBgeRuntime
from eck.memory.rag_store import PortableVectorStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and verify ECK local BGE RAG models.")
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()

    settings = Settings()
    model_dir = (args.model_dir or settings.rag_model_dir).resolve()
    runtime = LocalBgeRuntime(
        model_dir,
        embedding_model=settings.rag_embedding_model,
        reranker_model=settings.rag_reranker_model,
        device=settings.rag_device,
        allow_download=True,
    )
    try:
        vectors = runtime.embed(["金融 利率", "dog playing with a ball"])
        scores = runtime.rerank(
            "金融 利率",
            ["金融 利率 上升會提高借貸成本", "dog playing with a ball"],
        )
        with tempfile.TemporaryDirectory(prefix="eck-rag-smoke-") as temporary:
            vector_store = PortableVectorStore(
                Path(temporary) / "smoke.sqlite3",
                dimension=runtime.dimension,
            )
            vector_store.initialize()
            contents = ["金融 利率 上升會提高借貸成本", "dog playing with a ball"]
            for index, (content, embedding) in enumerate(zip(contents, vectors, strict=True)):
                vector_store.upsert(
                    {
                        "document_id": f"smoke:{index}",
                        "source_type": "smoke_test",
                        "title": f"Smoke {index}",
                        "content": content,
                        "source_uri": f"eck://smoke/{index}",
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "metadata": {},
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    embedding,
                )
            nearest = vector_store.search(vectors[0], limit=2)
            sqlite_integrity = vector_store.integrity()
        report = {
            "success": len(vectors) == 2
            and all(len(vector) == runtime.dimension for vector in vectors)
            and len(scores) == 2
            and scores[0] > scores[1]
            and nearest[0]["document_id"] == "smoke:0"
            and sqlite_integrity["valid"],
            "model_dir": str(model_dir),
            "dimension": runtime.dimension,
            "scores": scores,
            "sqlite_vec": sqlite_integrity,
            "status": runtime.status(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["success"] else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
