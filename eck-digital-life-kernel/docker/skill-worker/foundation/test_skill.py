from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def module():
    path = Path("/opt/eck-worker/foundation_skill.py")
    spec = importlib.util.spec_from_file_location("foundation_skill", path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def skill_name() -> str:
    return json.loads(Path("/request/manifest.json").read_text(encoding="utf-8"))["name"]


def test_foundation_skill_contract() -> None:
    skill = module()
    name = skill_name()
    context = {"skill_name": name, "output_dir": "/output", "permissions": []}
    if name == "data.advanced":
        result = skill.execute("analyze", {"records": [{"value": 1}, {"value": 3}]}, context)
        assert result["summary"]["value"]["mean"] == 2
    elif name == "social.connector":
        result = skill.execute("publish", {}, context)
        assert result["blocked"]
    else:
        assert callable(skill.execute)
