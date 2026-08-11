from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from eck.api.main import create_api
from eck.config import Settings
from eck.experimental.p6.mission_executor import DurableMissionExecutor as P6Executor
from eck.experimental.p7.federation import FederationService as P7FederationService
from eck.services.federation import FederationService as CompatibleFederationService
from eck.services.mission_executor import DurableMissionExecutor as CompatibleP6Executor


def _baseline() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return json.loads(
        (root / "config" / "architecture-baseline.json").read_text(encoding="utf-8")
    )


def _flatten_routes(api: Any) -> list[Any]:
    routes: list[Any] = []
    for route in api.routes:
        included = getattr(route, "original_router", None)
        routes.extend(included.routes if included is not None else (route,))
    return routes


def test_v010_rest_route_surface_is_preserved_exactly() -> None:
    baseline = _baseline()
    api = create_api(Settings(environment="test", auto_start_kernel=False))
    additive = set(baseline["post_v010_additive_routes"])
    rows = sorted(
        (
            ",".join(sorted(getattr(route, "methods", ()) or ())),
            route.path,
        )
        for route in _flatten_routes(api)
        if route.path not in additive
    )
    digest = hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    assert digest == baseline["rest_route_sha256_v010"]


def test_experimental_compatibility_facades_preserve_python_imports() -> None:
    assert CompatibleP6Executor is P6Executor
    assert CompatibleFederationService is P7FederationService


def test_architecture_checker_passes() -> None:
    from scripts.check_architecture import check

    root = Path(__file__).resolve().parents[2]
    assert check(root) == []

