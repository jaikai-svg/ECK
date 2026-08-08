from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

MODEL_ID = "zai-org/CogVideoX-2b"
REQUIRED_FILES = (
    "model_index.json",
    "scheduler/scheduler_config.json",
    "text_encoder/config.json",
    "tokenizer/tokenizer_config.json",
    "transformer/config.json",
    "vae/config.json",
)


def runtime_status(model_dir: Path) -> dict[str, Any]:
    checks = {relative: (model_dir / relative).is_file() for relative in REQUIRED_FILES}
    try:
        import torch

        cuda = torch.cuda.is_available()
        gpu = torch.cuda.get_device_name(0) if cuda else None
        torch_version = torch.__version__
        cuda_version = torch.version.cuda
    except ImportError:
        cuda = False
        gpu = None
        torch_version = None
        cuda_version = None
    return {
        "model": MODEL_ID,
        "model_dir": str(model_dir),
        "model_ready": all(checks.values()),
        "checks": checks,
        "cuda": cuda,
        "gpu": gpu,
        "torch": torch_version,
        "cuda_runtime": cuda_version,
        "local_only": True,
        "paid_api": False,
    }


def generate(request: dict[str, Any], output_path: Path) -> dict[str, Any]:
    import psutil
    import torch
    from diffusers import CogVideoXPipeline
    from diffusers.utils import export_to_video

    model_dir = Path(str(request["model_dir"])).resolve()
    status = runtime_status(model_dir)
    if not status["model_ready"]:
        raise FileNotFoundError(f"Incomplete CogVideoX model: {model_dir}")
    if not torch.cuda.is_available():
        raise RuntimeError("CogVideoX requires a CUDA GPU for this verified profile.")

    prompt = str(request.get("prompt", "")).strip()
    if len(prompt) < 3:
        raise ValueError("A descriptive video prompt is required.")
    frames = int(request.get("frames", 9))
    if frames < 9 or (frames - 1) % 8 != 0:
        raise ValueError("CogVideoX-2B frames must equal 8N+1 and be at least 9.")
    steps = max(1, int(request.get("steps", 25)))
    seed = int(request.get("seed", 31337))
    fps = max(1, int(request.get("fps", 8)))
    started = time.perf_counter()
    process = psutil.Process()
    memory_before_gb = process.memory_info().rss / 1024**3

    pipe = CogVideoXPipeline.from_pretrained(
        model_dir,
        torch_dtype=torch.float16,
        local_files_only=True,
        low_cpu_mem_usage=True,
    )
    pipe.enable_sequential_cpu_offload()
    pipe.vae.enable_slicing()
    pipe.vae.enable_tiling()
    pipe.set_progress_bar_config(disable=bool(request.get("quiet", False)))
    torch.cuda.reset_peak_memory_stats()
    generator = torch.Generator(device="cuda").manual_seed(seed)
    result = pipe(
        prompt=prompt,
        negative_prompt=str(request.get("negative_prompt", "")),
        height=480,
        width=720,
        num_frames=frames,
        num_inference_steps=steps,
        guidance_scale=float(request.get("guidance_scale", 6.0)),
        generator=generator,
    )
    video_frames = result.frames[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(video_frames, str(output_path), fps=fps)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("CogVideoX did not produce an MP4 artifact.")
    elapsed = time.perf_counter() - started
    duration = (frames - 1) / fps
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        "success": True,
        "artifact": str(output_path),
        "metadata": {
            "backend": "cogvideox",
            "model": MODEL_ID,
            "prompt": prompt,
            "seconds": round(duration, 3),
            "frames": frames,
            "fps": fps,
            "steps": steps,
            "seed": seed,
            "precision": "fp16",
            "offload": "sequential_cpu",
            "vae_slicing": True,
            "vae_tiling": True,
            "elapsed_seconds": round(elapsed, 3),
            "peak_gpu_memory_gb": round(
                torch.cuda.max_memory_reserved() / 1024**3,
                3,
            ),
            "process_memory_before_gb": round(memory_before_gb, 3),
            "process_memory_after_gb": round(
                process.memory_info().rss / 1024**3,
                3,
            ),
            "local_only": True,
            "paid_api": False,
        },
        "sha256": digest,
        "bytes": output_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request")
    parser.add_argument("--output")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--model-dir", default="workspace/cogvideo/model")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(runtime_status(Path(args.model_dir).resolve())))
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
