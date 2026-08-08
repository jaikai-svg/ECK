from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="workspace/framepack/source")
    args = parser.parse_args()
    source_dir = Path(args.source_dir).resolve()
    cache_dir = (source_dir / "hf_download").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_dir)
    repositories = (
        "hunyuanvideo-community/HunyuanVideo",
        "lllyasviel/flux_redux_bfl",
        "lllyasviel/FramePackI2V_HY",
    )
    downloaded = {}
    for repository in repositories:
        downloaded[repository] = snapshot_download(
            repository,
            cache_dir=cache_dir / "hub",
            max_workers=1,
        )
    print(json.dumps(downloaded, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
