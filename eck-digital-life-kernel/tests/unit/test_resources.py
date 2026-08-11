from __future__ import annotations

import eck.runtime.resources as resource_module
from eck.runtime.resources import SystemResourceMonitor


def test_project_scan_reports_logical_size_and_breakdown(settings) -> None:
    settings.prepare_directories()
    model = settings.workspace_dir / "models" / "demo.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model-bytes")
    document = settings.workspace_dir.parent / "docs" / "note.txt"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"documentation")
    monitor = SystemResourceMonitor(settings)

    result = monitor.project_snapshot(force=True)

    assert result["measurement"] == "logical_readable_file_size"
    assert result["logical_bytes"] >= len(b"model-bytes") + len(b"documentation")
    assert result["file_count"] >= 2
    assert {item["name"] for item in result["breakdown"]} >= {"workspace", "docs"}
    assert result["cached"] is False
    assert monitor.project_snapshot()["cached"] is True
    cached = monitor.cached_project_snapshot()
    assert cached["available"] is True
    assert cached["logical_bytes"] == result["logical_bytes"]
    restored = SystemResourceMonitor(settings).project_snapshot()
    assert restored["cached"] is True
    assert restored["logical_bytes"] == result["logical_bytes"]


def test_cached_project_snapshot_never_starts_a_scan(settings, monkeypatch) -> None:
    monitor = SystemResourceMonitor(settings)
    monkeypatch.setattr(
        monitor,
        "_scan_project",
        lambda: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    result = monitor.cached_project_snapshot()

    assert result == {
        "available": False,
        "logical_bytes": None,
        "logical_gb": None,
        "file_count": None,
        "scanned_at": None,
        "cached": True,
        "stale": True,
    }


def test_resource_pressure_only_throttles_critical_background_work(settings) -> None:
    monitor = SystemResourceMonitor(settings)
    normal = monitor._pressure(
        {
            "available_bytes": 8 * 1024**3,
            "used_percent": 50.0,
        },
        {
            "free_bytes": 100 * 1024**3,
            "used_percent": 40.0,
        },
    )
    critical = monitor._pressure(
        {
            "available_bytes": 512 * 1024**2,
            "used_percent": 97.0,
        },
        {
            "free_bytes": 100 * 1024**3,
            "used_percent": 40.0,
        },
    )

    assert normal["background_allowed"] is True
    assert normal["level"] == "normal"
    assert critical["background_allowed"] is False
    assert critical["level"] == "critical"
    assert "available_memory_below_background_floor" in critical["reasons"]

    high = monitor._pressure(
        {"available_bytes": 3 * 1024**3, "used_percent": 91.0},
        {"free_bytes": 100 * 1024**3, "used_percent": 40.0},
    )
    moderate = monitor._pressure(
        {"available_bytes": 8 * 1024**3, "used_percent": 82.0},
        {"free_bytes": 100 * 1024**3, "used_percent": 40.0},
    )
    disk_critical = monitor._pressure(
        {"available_bytes": 8 * 1024**3, "used_percent": 50.0},
        {"free_bytes": 512 * 1024**2, "used_percent": 99.0},
    )
    assert high["level"] == "high"
    assert moderate["level"] == "moderate"
    assert disk_critical["level"] == "critical"
    assert "project_drive_free_space_below_floor" in disk_critical["reasons"]
    assert "project_drive_capacity_near_exhaustion" in disk_critical["reasons"]


def test_quick_snapshot_is_cached_and_handles_unavailable_disk(
    settings, monkeypatch
) -> None:
    monitor = SystemResourceMonitor(settings)
    monkeypatch.setattr(
        monitor,
        "_memory_status",
        lambda: SystemResourceMonitor._memory_result(
            16 * 1024**3,
            8 * 1024**3,
            12 * 1024**3,
            32 * 1024**3,
        ),
    )
    monkeypatch.setattr(
        monitor,
        "_process_status",
        lambda: {
            "pid": 1,
            "working_set_bytes": 128 * 1024**2,
            "private_bytes": 64 * 1024**2,
            "pagefile_bytes": 64 * 1024**2,
        },
    )

    def unavailable_disk(_path):
        raise OSError("drive unavailable")

    monkeypatch.setattr(resource_module.shutil, "disk_usage", unavailable_disk)
    first = monitor.snapshot(include_project=False)
    second = monitor.quick_snapshot()
    allowed, pressure = monitor.background_allowed()

    assert "project" not in first
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["host"]["disk"]["total_bytes"] is None
    assert allowed is True
    assert pressure["level"] == "normal"
    assert SystemResourceMonitor._empty_memory()["total_bytes"] is None


def test_project_scan_records_root_access_error(settings, monkeypatch) -> None:
    monitor = SystemResourceMonitor(settings)

    def denied_scan(_path):
        raise OSError("access denied")

    monkeypatch.setattr(resource_module.os, "scandir", denied_scan)
    result = monitor._scan_project()

    assert result["logical_bytes"] == 0
    assert result["scan_errors"] == 1
    buckets: dict[str, dict[str, int]] = {}
    monitor._add_error(buckets, None)
    monitor._add_error(buckets, "denied")
    assert buckets["denied"]["errors"] == 1
