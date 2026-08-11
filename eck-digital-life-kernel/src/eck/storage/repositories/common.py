from __future__ import annotations

import json
from typing import Any

GENESIS_HASH = "0" * 64


def to_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)

