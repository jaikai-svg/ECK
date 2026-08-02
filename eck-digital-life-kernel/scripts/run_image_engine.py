from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
        return
    if args.request is None or args.output is None:
        parser.error("--request and --output are required unless --serve is used")
    request = json.loads(args.request.read_text(encoding="utf-8"))
    report = generate(request, args.output.resolve())
    print(json.dumps(report, ensure_ascii=False))


def serve() -> None:
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command.get("command") == "shutdown":
                print(json.dumps({"success": True, "stopped": True}), flush=True)
                return
            request_path = Path(str(command["request"])).resolve()
            output_path = Path(str(command["output"])).resolve()
            request = json.loads(request_path.read_text(encoding="utf-8"))
            report = generate(request, output_path)
        except Exception as exc:
            report = {
                "success": False,
                "error": type(exc).__name__,
                "detail": str(exc),
            }
        print(json.dumps(report, ensure_ascii=False), flush=True)


def generate(request: dict[str, Any], output_path: Path) -> dict[str, Any]:
    import torch
    from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

    model_dir = Path(str(request["model_dir"])).resolve()
    if not (model_dir / "eck-model.json").is_file():
        raise FileNotFoundError(f"Stable Diffusion model is not installed: {model_dir}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to the image engine.")

    prompt = str(request.get("prompt", "")).strip()
    if len(prompt) < 3:
        raise ValueError("A descriptive image prompt is required.")
    negative_prompt = str(request.get("negative_prompt", "")).strip()
    width = _dimension(request.get("width", 512))
    height = _dimension(request.get("height", 512))
    steps = min(50, max(20, int(request.get("steps", 32))))
    guidance_scale = min(12.0, max(1.0, float(request.get("guidance_scale", 7.5))))
    seed = int(request.get("seed") or secrets.randbelow(2**31 - 1))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    torch.set_grad_enabled(False)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    pipeline = StableDiffusionPipeline.from_pretrained(
        model_dir,
        variant="fp16",
        torch_dtype=torch.float16,
        use_safetensors=True,
        local_files_only=True,
    )
    pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
        pipeline.scheduler.config,
        algorithm_type="dpmsolver++",
        use_karras_sigmas=True,
    )
    pipeline.enable_model_cpu_offload()
    pipeline.enable_attention_slicing()
    pipeline.vae.enable_slicing()
    pipeline.vae.enable_tiling()
    generator = torch.Generator(device="cuda").manual_seed(seed)
    try:
        with torch.inference_mode():
            result = pipeline(
                prompt=prompt,
                negative_prompt=negative_prompt or None,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                generator=generator,
                num_images_per_prompt=1,
            )
        flagged = bool(result.nsfw_content_detected and result.nsfw_content_detected[0])
        if flagged:
            return {
                "success": False,
                "safety_blocked": True,
                "detail": "The Stable Diffusion safety checker blocked this image.",
            }
        image = result.images[0]
        image.save(output_path, format="PNG", optimize=True)
        elapsed = time.perf_counter() - started
        metadata = {
            "model": "stable-diffusion-v1-5/stable-diffusion-v1-5",
            "variant": "fp16",
            "scheduler": "DPM++ Karras",
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "steps": steps,
            "guidance_scale": guidance_scale,
            "width": width,
            "height": height,
            "elapsed_seconds": round(elapsed, 3),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
            "device": torch.cuda.get_device_name(0),
        }
        output_path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "success": True,
            "artifact": str(output_path),
            "metadata": metadata,
        }
    finally:
        del pipeline
        torch.cuda.empty_cache()


def _dimension(value: object) -> int:
    dimension = min(512, max(256, int(value)))
    return dimension - dimension % 8


if __name__ == "__main__":
    main()
