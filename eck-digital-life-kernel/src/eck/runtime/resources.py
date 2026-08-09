from __future__ import annotations

import ctypes
import json
import os
import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from eck.config import Settings
from eck.core.time import iso_now


class SystemResourceMonitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.project_root = settings.workspace_dir.resolve().parent
        self._sample_lock = threading.Lock()
        self._project_lock = threading.Lock()
        self._sample_cache: dict[str, Any] | None = None
        self._sampled_at = 0.0
        self._project_cache: dict[str, Any] | None = None
        self._project_scanned_at = 0.0
        self._project_cache_path = settings.data_dir.resolve() / "project-size-cache.json"
        self._restore_project_cache()

    def snapshot(
        self,
        *,
        include_project: bool = True,
        force_project: bool = False,
    ) -> dict[str, Any]:
        result = self.quick_snapshot()
        if include_project:
            result["project"] = self.project_snapshot(force=force_project)
        return result

    def quick_snapshot(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._sample_lock:
            if (
                not force
                and self._sample_cache is not None
                and now - self._sampled_at < self.settings.resource_sample_seconds
            ):
                return self._copy_quick(self._sample_cache, cached=True)
            memory = self._memory_status()
            disk = self._disk_status()
            process = self._process_status()
            pressure = self._pressure(memory, disk)
            sample = {
                "enabled": self.settings.resource_monitor_enabled,
                "sampled_at": iso_now(),
                "host": {"memory": memory, "disk": disk},
                "process": process,
                "pressure": pressure,
            }
            self._sample_cache = sample
            self._sampled_at = now
            return self._copy_quick(sample, cached=False)

    def background_allowed(self) -> tuple[bool, dict[str, Any]]:
        snapshot = self.quick_snapshot()
        pressure = cast(dict[str, Any], snapshot["pressure"])
        return bool(pressure["background_allowed"]), pressure

    def project_snapshot(self, *, force: bool = False) -> dict[str, Any]:
        with self._project_lock:
            now = time.monotonic()
            if (
                not force
                and self._project_cache is not None
                and now - self._project_scanned_at
                < self.settings.resource_project_scan_seconds
            ):
                return self._project_result(self._project_cache, cached=True)
            result = self._scan_project()
            self._project_cache = result
            self._project_scanned_at = time.monotonic()
            self._persist_project_cache(result)
            return self._project_result(result, cached=False)

    def _restore_project_cache(self) -> None:
        try:
            result = json.loads(self._project_cache_path.read_text(encoding="utf-8"))
            scanned_at = datetime.fromisoformat(str(result["scanned_at"]))
        except (KeyError, OSError, TypeError, ValueError):
            return
        if not isinstance(result, dict) or result.get("root") != str(self.project_root):
            return
        if scanned_at.tzinfo is None:
            scanned_at = scanned_at.replace(tzinfo=UTC)
        age = max(0.0, (datetime.now(UTC) - scanned_at.astimezone(UTC)).total_seconds())
        if age >= self.settings.resource_project_scan_seconds:
            return
        self._project_cache = result
        self._project_scanned_at = time.monotonic() - age

    def _persist_project_cache(self, result: dict[str, Any]) -> None:
        temporary = self._project_cache_path.with_suffix(".tmp")
        try:
            self._project_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._project_cache_path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _scan_project(self) -> dict[str, Any]:
        started = time.perf_counter()
        total_bytes = 0
        file_count = 0
        scan_errors = 0
        top: dict[str, dict[str, int]] = {}
        workspace: dict[str, dict[str, int]] = {}
        stack: list[tuple[Path, str | None, str | None]] = [
            (self.project_root, None, None)
        ]

        while stack:
            path, top_name, workspace_name = stack.pop()
            try:
                entries = os.scandir(path)
            except OSError:
                scan_errors += 1
                self._add_error(top, top_name)
                self._add_error(workspace, workspace_name)
                continue
            with entries:
                for entry in entries:
                    entry_top = top_name
                    entry_workspace = workspace_name
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        scan_errors += 1
                        self._add_error(top, entry_top)
                        self._add_error(workspace, entry_workspace)
                        continue
                    if path == self.project_root:
                        entry_top = entry.name if is_directory else "[root files]"
                    elif (
                        top_name == self.settings.workspace_dir.name
                        and path == self.settings.workspace_dir.resolve()
                    ):
                        entry_workspace = entry.name if is_directory else "[workspace files]"
                    try:
                        if entry.is_symlink():
                            continue
                        if is_directory:
                            stack.append((Path(entry.path), entry_top, entry_workspace))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        scan_errors += 1
                        self._add_error(top, entry_top)
                        self._add_error(workspace, entry_workspace)
                        continue
                    total_bytes += size
                    file_count += 1
                    self._add_file(top, entry_top, size)
                    self._add_file(workspace, entry_workspace, size)

        return {
            "root": str(self.project_root),
            "measurement": "logical_readable_file_size",
            "logical_bytes": total_bytes,
            "logical_gb": round(total_bytes / 1024**3, 2),
            "file_count": file_count,
            "scan_errors": scan_errors,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "scanned_at": iso_now(),
            "breakdown": self._bucket_result(top),
            "workspace_breakdown": self._bucket_result(workspace),
        }

    @staticmethod
    def _add_file(buckets: dict[str, dict[str, int]], name: str | None, size: int) -> None:
        if name is None:
            return
        bucket = buckets.setdefault(name, {"logical_bytes": 0, "file_count": 0, "errors": 0})
        bucket["logical_bytes"] += size
        bucket["file_count"] += 1

    @staticmethod
    def _add_error(buckets: dict[str, dict[str, int]], name: str | None) -> None:
        if name is None:
            return
        bucket = buckets.setdefault(name, {"logical_bytes": 0, "file_count": 0, "errors": 0})
        bucket["errors"] += 1

    @staticmethod
    def _bucket_result(buckets: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                **values,
                "logical_gb": round(values["logical_bytes"] / 1024**3, 2),
            }
            for name, values in sorted(
                buckets.items(), key=lambda item: item[1]["logical_bytes"], reverse=True
            )
        ]

    def _project_result(self, result: dict[str, Any], *, cached: bool) -> dict[str, Any]:
        return {
            **result,
            "cached": cached,
            "age_seconds": round(max(0.0, time.monotonic() - self._project_scanned_at), 1),
            "cache_seconds": self.settings.resource_project_scan_seconds,
        }

    @staticmethod
    def _copy_quick(result: dict[str, Any], *, cached: bool) -> dict[str, Any]:
        return {
            **result,
            "cached": cached,
            "host": {**cast(dict[str, Any], result["host"])},
            "process": {**cast(dict[str, Any], result["process"])},
            "pressure": {**cast(dict[str, Any], result["pressure"])},
        }

    def _pressure(
        self,
        memory: dict[str, Any],
        disk: dict[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        available = memory.get("available_bytes")
        memory_percent = memory.get("used_percent")
        disk_free = disk.get("free_bytes")
        disk_percent = disk.get("used_percent")
        minimum_ram = int(self.settings.resource_background_min_available_ram_gb * 1024**3)
        minimum_disk = int(self.settings.resource_background_min_disk_free_gb * 1024**3)
        level = "normal"

        if isinstance(available, int) and available < minimum_ram:
            level = "critical"
            reasons.append("available_memory_below_background_floor")
        if isinstance(memory_percent, float) and memory_percent >= 96:
            level = "critical"
            reasons.append("physical_memory_near_exhaustion")
        if isinstance(disk_free, int) and disk_free < minimum_disk:
            level = "critical"
            reasons.append("project_drive_free_space_below_floor")
        if isinstance(disk_percent, float) and disk_percent >= 98:
            level = "critical"
            reasons.append("project_drive_capacity_near_exhaustion")

        if level != "critical":
            if (
                (isinstance(available, int) and available < minimum_ram * 2)
                or (isinstance(memory_percent, float) and memory_percent >= 90)
                or (isinstance(disk_free, int) and disk_free < minimum_disk * 2)
                or (isinstance(disk_percent, float) and disk_percent >= 95)
            ):
                level = "high"
            elif (
                (isinstance(memory_percent, float) and memory_percent >= 80)
                or (isinstance(disk_percent, float) and disk_percent >= 90)
            ):
                level = "moderate"

        background_allowed = not self.settings.resource_monitor_enabled or level != "critical"
        details = {
            "normal": "資源在背景工作安全範圍內。",
            "moderate": "資源使用偏高，持續觀察但不停止背景工作。",
            "high": "資源壓力高；避免同時啟動多個大型模型。",
            "critical": "資源已達保護門檻，暫緩非緊急背景工作以避免換頁與系統卡頓。",
        }
        return {
            "level": level,
            "background_allowed": background_allowed,
            "reasons": reasons,
            "detail": details[level],
            "minimum_background_available_ram_gb": (
                self.settings.resource_background_min_available_ram_gb
            ),
            "minimum_project_drive_free_gb": (
                self.settings.resource_background_min_disk_free_gb
            ),
        }

    def _disk_status(self) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(self.project_root)
        except OSError:
            return {
                "root": str(self.project_root),
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "used_percent": None,
                "active_time_available": False,
            }
        return {
            "root": str(self.project_root),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
            "active_time_available": False,
        }

    @staticmethod
    def _memory_status() -> dict[str, Any]:
        if os.name != "nt":
            sysconf_value = getattr(os, "sysconf", None)
            if not callable(sysconf_value):
                return SystemResourceMonitor._empty_memory()
            sysconf = cast(Callable[[str], int], sysconf_value)
            try:
                page_size = sysconf("SC_PAGE_SIZE")
                total = page_size * sysconf("SC_PHYS_PAGES")
                available = page_size * sysconf("SC_AVPHYS_PAGES")
            except (AttributeError, OSError, ValueError):
                return SystemResourceMonitor._empty_memory()
            return SystemResourceMonitor._memory_result(total, available, None, None)

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        memory_status = kernel32.GlobalMemoryStatusEx
        memory_status.argtypes = [ctypes.POINTER(MemoryStatus)]
        memory_status.restype = ctypes.c_int
        if not memory_status(ctypes.byref(status)):
            return SystemResourceMonitor._empty_memory()
        commit_used = int(status.total_page_file - status.available_page_file)
        return SystemResourceMonitor._memory_result(
            int(status.total_physical),
            int(status.available_physical),
            commit_used,
            int(status.total_page_file),
        )

    @staticmethod
    def _memory_result(
        total: int,
        available: int,
        commit_used: int | None,
        commit_limit: int | None,
    ) -> dict[str, Any]:
        used = max(0, total - available)
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "used_percent": round(used / total * 100, 1) if total else 0.0,
            "commit_used_bytes": commit_used,
            "commit_limit_bytes": commit_limit,
            "commit_used_percent": (
                round(commit_used / commit_limit * 100, 1)
                if commit_used is not None and commit_limit
                else None
            ),
        }

    @staticmethod
    def _empty_memory() -> dict[str, Any]:
        return {
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "used_percent": None,
            "commit_used_bytes": None,
            "commit_limit_bytes": None,
            "commit_used_percent": None,
        }

    @staticmethod
    def _process_status() -> dict[str, Any]:
        result: dict[str, Any] = {
            "pid": os.getpid(),
            "working_set_bytes": None,
            "private_bytes": None,
            "pagefile_bytes": None,
        }
        if os.name != "nt":
            try:
                pages = Path("/proc/self/statm").read_text(encoding="ascii").split()
                sysconf_value = getattr(os, "sysconf", None)
                if not callable(sysconf_value):
                    return result
                sysconf = cast(Callable[[str], int], sysconf_value)
                page_size = sysconf("SC_PAGE_SIZE")
                result["working_set_bytes"] = int(pages[1]) * page_size
            except (AttributeError, OSError, ValueError, IndexError):
                pass
            return result

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
                ("private_usage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        get_process_memory = psapi.GetProcessMemoryInfo
        get_process_memory.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCountersEx),
            ctypes.c_ulong,
        ]
        get_process_memory.restype = ctypes.c_int
        if get_process_memory(
            get_current_process(), ctypes.byref(counters), counters.cb
        ):
            result.update(
                {
                    "working_set_bytes": int(counters.working_set_size),
                    "private_bytes": int(counters.private_usage),
                    "pagefile_bytes": int(counters.pagefile_usage),
                }
            )
        return result
