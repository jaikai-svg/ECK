from __future__ import annotations

import argparse
import json
from pathlib import Path

from huggingface_hub import model_info, snapshot_download

MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"
ALLOW_PATTERNS = (
    "README.md",
    "model_index.json",
    "feature_extractor/preprocessor_config.json",
    "safety_checker/config.json",
    "safety_checker/model.fp16.safetensors",
    "scheduler/scheduler_config.json",
    "text_encoder/config.json",
    "text_encoder/model.fp16.safetensors",
    "tokenizer/merges.txt",
    "tokenizer/special_tokens_map.json",
    "tokenizer/tokenizer_config.json",
    "tokenizer/vocab.json",
    "unet/config.json",
    "unet/diffusion_pytorch_model.fp16.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.fp16.safetensors",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    target = args.target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    info = model_info(MODEL_ID)
    snapshot_download(
        repo_id=MODEL_ID,
        revision=info.sha,
        local_dir=target,
        allow_patterns=list(ALLOW_PATTERNS),
    )
    manifest = {
        "model_id": MODEL_ID,
        "revision": info.sha,
        "variant": "fp16",
        "format": "safetensors",
        "license": "creativeml-openrail-m",
        "files": list(ALLOW_PATTERNS),
    }
    (target / "eck-model.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
