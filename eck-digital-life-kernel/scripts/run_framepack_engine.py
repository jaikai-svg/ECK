from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


def runtime_status(source_dir: Path) -> dict[str, Any]:
    cache = source_dir / "hf_download" / "hub"
    models = {
        "hunyuan_video": cache / "models--hunyuanvideo-community--HunyuanVideo",
        "siglip": cache / "models--lllyasviel--flux_redux_bfl",
        "framepack_i2v": cache / "models--lllyasviel--FramePackI2V_HY",
    }
    return {
        "source": (source_dir / "demo_gradio.py").is_file(),
        "models": {name: path.is_dir() for name, path in models.items()},
    }


def load_framepack(source_dir: Path) -> dict[str, Any]:
    demo_path = source_dir / "demo_gradio.py"
    source = demo_path.read_text(encoding="utf-8")
    marker = "\nquick_prompts ="
    if marker not in source:
        raise RuntimeError("Unsupported FramePack source layout.")
    namespace: dict[str, Any] = {
        "__file__": str(demo_path),
        "__name__": "eck_framepack_runtime",
    }
    prior_argv = sys.argv
    prior_cwd = Path.cwd()
    source_path = str(source_dir)
    inserted_path = source_path not in sys.path
    if inserted_path:
        sys.path.insert(0, source_path)
    sys.argv = [str(demo_path)]
    os.chdir(source_dir)
    try:
        exec(compile(source.split(marker, 1)[0], str(demo_path), "exec"), namespace)
    finally:
        sys.argv = prior_argv
        os.chdir(prior_cwd)
        if inserted_path:
            sys.path.remove(source_path)
    return namespace


def generate(request: dict[str, Any], output_path: Path) -> dict[str, Any]:
    source_dir = Path(str(request["source_dir"])).resolve()
    input_path = Path(str(request["input_image"])).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"FramePack input image not found: {input_path}")
    import numpy as np
    from PIL import Image

    started = time.perf_counter()
    runtime = load_framepack(source_dir)
    image = np.asarray(Image.open(input_path).convert("RGB"))
    worker = runtime["worker"]
    stream = runtime["stream"]
    prior_cwd = Path.cwd()
    os.chdir(source_dir)
    try:
        worker(
            image,
            str(request["prompt"]),
            str(request.get("negative_prompt", "")),
            int(request.get("seed", 31337)),
            float(request.get("seconds", 3.0)),
            9,
            int(request.get("steps", 25)),
            1.0,
            10.0,
            0.0,
            float(request.get("gpu_memory_preservation", 6.0)),
            bool(request.get("use_teacache", False)),
            int(request.get("mp4_crf", 16)),
        )
        generated: Path | None = None
        while True:
            flag, data = stream.output_queue.next()
            if flag == "file":
                generated = (source_dir / str(data)).resolve()
            if flag == "end":
                break
    finally:
        os.chdir(prior_cwd)
    if generated is None or not generated.is_file():
        raise RuntimeError("FramePack did not produce an MP4 artifact.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(generated, output_path)
    return {
        "success": True,
        "artifact": str(output_path),
        "metadata": {
            "backend": "framepack",
            "model": "lllyasviel/FramePackI2V_HY",
            "input_image": str(input_path),
            "prompt": request["prompt"],
            "seconds": float(request.get("seconds", 3.0)),
            "fps": 30,
            "steps": int(request.get("steps", 25)),
            "seed": int(request.get("seed", 31337)),
            "teacache": bool(request.get("use_teacache", False)),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "local_only": True,
            "paid_api": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request")
    parser.add_argument("--output")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--source-dir", default="workspace/framepack/source")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(runtime_status(Path(args.source_dir).resolve())))
        return 0
    if not args.request or not args.output:
        parser.error("--request and --output are required")
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        result = generate(request, Path(args.output).resolve())
    except Exception as exc:
        result = {
            "success": False,
            "error": type(exc).__name__,
            "detail": str(exc),
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
