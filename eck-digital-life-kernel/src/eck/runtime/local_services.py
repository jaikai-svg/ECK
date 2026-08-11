from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from eck.config import Settings


class LocalServiceManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ollama_process: asyncio.subprocess.Process | None = None
        self._ollama_lock = asyncio.Lock()
        self._ollama_started_by_eck = False
        self._last_error = ""

    async def ensure_ollama(self) -> bool:
        if self.settings.brain_provider != "ollama":
            return True
        if await self._ollama_health():
            self._last_error = ""
            return True
        if not self.settings.ollama_auto_start:
            self._last_error = "Ollama is offline and automatic startup is disabled."
            return False
        async with self._ollama_lock:
            if await self._ollama_health():
                self._last_error = ""
                return True
            executable = self._ollama_executable()
            if executable is None:
                self._last_error = "Ollama executable was not found."
                return False
            if self._ollama_process is None or self._ollama_process.returncode is not None:
                self._ollama_process = await asyncio.create_subprocess_exec(
                    executable,
                    "serve",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._ollama_started_by_eck = True
            deadline = (
                asyncio.get_running_loop().time()
                + self.settings.ollama_startup_timeout_seconds
            )
            while asyncio.get_running_loop().time() < deadline:
                if await self._ollama_health():
                    self._last_error = ""
                    return True
                if self._ollama_process.returncode is not None:
                    break
                await asyncio.sleep(0.5)
            self._last_error = "Ollama did not become ready before the startup deadline."
            return False

    async def unload_models(self) -> None:
        models = {
            item
            for item in (
                self.settings.ollama_model,
                self.settings.coder_model,
                self.settings.supervisor_model,
            )
            if item
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            for model in models:
                try:
                    await client.post(
                        f"{self.settings.ollama_base_url.rstrip('/')}/api/generate",
                        json={"model": model, "keep_alive": 0},
                    )
                except httpx.HTTPError:
                    continue

    async def stop_owned(self) -> None:
        await self.unload_models()
        process = self._ollama_process
        if not self._ollama_started_by_eck or process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
        self._ollama_process = None
        self._ollama_started_by_eck = False

    def status(self) -> dict[str, Any]:
        process = self._ollama_process
        return {
            "ollama": {
                "auto_start": self.settings.ollama_auto_start,
                "started_by_eck": self._ollama_started_by_eck,
                "owned_process_running": bool(process and process.returncode is None),
                "keep_alive": self.settings.ollama_keep_alive,
                "last_error": self._last_error,
            },
            "policy": {
                "forge_start": "on_image_demand",
                "model_residency": "workload_managed",
                "shutdown_scope": "eck_owned_processes_only",
            },
        }

    async def _ollama_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/tags"
                )
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _ollama_executable(self) -> str | None:
        configured = self.settings.ollama_executable
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return str(path.resolve())
        discovered = shutil.which("ollama")
        if discovered:
            return discovered
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.is_file():
                return str(candidate.resolve())
        return None
