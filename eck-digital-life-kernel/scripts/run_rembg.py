from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="birefnet-general")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.model)
        return
    if args.input is None or args.output is None:
        parser.error("--input and --output are required unless --serve is used")
    report = remove_background(args.input.resolve(), args.output.resolve(), args.model)
    print(json.dumps(report, ensure_ascii=False))


def serve(default_model: str) -> None:
    session: Any | None = None
    session_model = ""
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command.get("command") == "shutdown":
                print(json.dumps({"success": True, "stopped": True}), flush=True)
                return
            model = str(command.get("model") or default_model)
            if session is None or session_model != model:
                session = create_session(model)
                session_model = model
            report = remove_background(
                Path(str(command["input"])).resolve(),
                Path(str(command["output"])).resolve(),
                model,
                session=session,
            )
        except Exception as exc:
            report = {
                "success": False,
                "error": type(exc).__name__,
                "detail": str(exc),
            }
        print(json.dumps(report, ensure_ascii=False), flush=True)


def create_session(model: str) -> Any:
    from rembg import new_session

    return new_session(model, providers=["CPUExecutionProvider"])


def remove_background(
    input_path: Path,
    output_path: Path,
    model: str,
    *,
    session: Any | None = None,
) -> dict[str, Any]:
    from PIL import Image
    from rembg import remove

    if not input_path.is_file():
        raise FileNotFoundError(f"Input image not found: {input_path}")
    model_home = Path(os.environ.get("U2NET_HOME", "~/.u2net")).expanduser().resolve()
    expected_model = model_home / f"{model}.onnx"
    if not expected_model.is_file():
        raise FileNotFoundError(f"rembg model is not installed: {expected_model}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    active_session = session or create_session(model)
    with Image.open(input_path) as source:
        result = remove(source.convert("RGBA"), session=active_session)
        if not isinstance(result, Image.Image):
            raise RuntimeError("rembg returned an invalid image result.")
        result.save(output_path, format="PNG", optimize=True)
        width, height = result.size
    metadata = {
        "model": model,
        "provider": "CPUExecutionProvider",
        "width": width,
        "height": height,
        "transparent_background": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "local_only": True,
        "paid_api": False,
    }
    return {"success": True, "artifact": str(output_path), "metadata": metadata}


if __name__ == "__main__":
    main()
