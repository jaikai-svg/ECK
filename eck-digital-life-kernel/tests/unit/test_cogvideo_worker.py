from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_worker() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_cogvideo_engine.py"
    spec = importlib.util.spec_from_file_location("run_cogvideo_engine", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portrait_output_center_crops_native_cogvideo_frames() -> None:
    worker = _load_worker()

    crop_box = worker.output_crop_box(720, 480, 432, 768)

    assert crop_box == (225, 0, 495, 480)


def test_native_ratio_preserves_full_frame() -> None:
    worker = _load_worker()

    crop_box = worker.output_crop_box(720, 480, 720, 480)

    assert crop_box == (0, 0, 720, 480)


def test_color_channel_collapse_is_rejected() -> None:
    worker = _load_worker()

    with pytest.raises(RuntimeError, match="color-channel artifact"):
        worker.validate_frame_quality(
            {
                "edge_energy_mean": 1.0,
                "temporal_delta_mean": 1.0,
                "channel_mean_spread": 212.0,
                "channel_std_min": 1.9,
            }
        )
