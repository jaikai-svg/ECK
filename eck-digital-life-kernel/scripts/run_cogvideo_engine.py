from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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


def frame_quality_metrics(video_frames: list[Any]) -> dict[str, float]:
    import numpy as np

    frames = np.stack([np.asarray(frame).astype(np.float32) for frame in video_frames])
    gray = frames.mean(axis=3)
    edge_x = np.abs(np.diff(gray, axis=2)).mean()
    edge_y = np.abs(np.diff(gray, axis=1)).mean()
    temporal_delta = np.abs(np.diff(frames, axis=0)).mean()
    return {
        "edge_energy_mean": round(float((edge_x + edge_y) / 2), 4),
        "temporal_delta_mean": round(float(temporal_delta), 4),
    }


def validate_frame_quality(metrics: dict[str, float]) -> None:
    if metrics["edge_energy_mean"] < 0.35:
        raise RuntimeError(
            "Generated frames collapsed into a near-featureless image; artifact rejected."
        )
    if metrics["temporal_delta_mean"] < 0.2:
        raise RuntimeError("Generated frames contain no meaningful motion; artifact rejected.")


def export_faststart(video_frames: list[Any], output_path: Path, fps: int) -> bool:
    import imageio_ffmpeg
    from diffusers.utils import export_to_video

    encoded_path = output_path.with_suffix(".encoded.mp4")
    encoded_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    export_to_video(video_frames, str(encoded_path), fps=fps)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(encoded_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        encoded_path.replace(output_path)
        return False
    else:
        encoded_path.unlink(missing_ok=True)
        return True


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
    width = int(request.get("width", 720))
    height = int(request.get("height", 480))
    if not (256 <= width <= 1024 and 256 <= height <= 1024):
        raise ValueError("CogVideoX dimensions must be between 256 and 1024 pixels.")
    if width % 8 or height % 8 or width * height > 600_000:
        raise ValueError(
            "CogVideoX dimensions must be divisible by 8 and stay within 600000 pixels."
        )
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
        height=height,
        width=width,
        num_frames=frames,
        num_inference_steps=steps,
        guidance_scale=float(request.get("guidance_scale", 6.0)),
        generator=generator,
    )
    video_frames = result.frames[0]
    quality = frame_quality_metrics(video_frames)
    validate_frame_quality(quality)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    container_faststart = export_faststart(video_frames, output_path, fps)
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
            "width": width,
            "height": height,
            "steps": steps,
            "seed": seed,
            "precision": "fp16",
            "offload": "sequential_cpu",
            "vae_slicing": True,
            "vae_tiling": True,
            "container_faststart": container_faststart,
            "frame_quality": quality,
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
