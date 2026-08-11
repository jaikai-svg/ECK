from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.services.project_lab_components.base import ProjectLabMixinBase
from eck.services.project_lab_components.github_policy import GitHubCommandPolicy


class ProjectLabGitHubMixin(ProjectLabMixinBase):
    async def publish(self, project_id: str) -> dict[str, Any]:
        manifest = self.get_project(project_id)
        if manifest.get("status") not in {"verified", "publish_failed"}:
            raise RuntimeError("Only verified autonomous projects can be published.")
        if not self.settings.github_publish_enabled:
            return self._publish_deferred(manifest, "GitHub publishing is disabled.")
        github = self.github_status()
        if not github["ready"]:
            return self._publish_deferred(manifest, str(github["detail"]))
        source_dir = Path(str(manifest["source_dir"]))
        self._scan_secrets(source_dir)
        self._initialize_git(source_dir)
        account = self.settings.github_account or str(github["account"])
        repository = f"{account}/{manifest['name']}"
        visibility = str(manifest.get("visibility", "private"))
        executable = str(github["executable"])
        token = self._github_token(executable, account)
        if token is None:
            return self._publish_deferred(
                manifest,
                f"GitHub credentials for the dedicated account {account!r} are unavailable.",
            )
        result = await self._run_process(
            [
                executable,
                "repo",
                "create",
                repository,
                f"--{visibility}",
                "--source",
                str(source_dir),
                "--remote",
                "origin",
                "--push",
            ],
            cwd=source_dir,
            timeout=180,
            env=self._safe_git_environment(
                self._github_environment(token, executable),
                source_dir,
            ),
        )
        manifest["github"] = {
            "published": result["returncode"] == 0,
            "repository": repository,
            "url": f"https://github.com/{repository}",
            "detail": result["output_tail"],
        }
        manifest["status"] = "published" if result["returncode"] == 0 else "publish_failed"
        manifest["updated_at"] = utc_now().isoformat()
        self._write_manifest(self._project_dir(project_id), manifest)
        await self.events.publish(
            "AutonomousProjectPublished" if result["returncode"] == 0 else "ProjectPublishFailed",
            project_id,
            {"repository": repository, "returncode": result["returncode"]},
            correlation_id=project_id,
        )
        return manifest

    async def publish_directory(
        self,
        *,
        name: str,
        source_dir: Path,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        """Publish an already verified project without coupling it to project-lab manifests."""
        if not self.settings.github_publish_enabled:
            return {
                "published": False,
                "deferred": True,
                "detail": "GitHub publishing is disabled.",
            }
        if not source_dir.is_dir():
            raise ValueError("Verified project source directory is missing.")
        safe_name = self._safe_project_name(name, new_id("project"))
        selected_visibility = visibility or self.settings.github_default_visibility
        if selected_visibility not in {"private", "public"}:
            raise ValueError("GitHub visibility must be private or public.")
        github = self.github_status()
        if not github["ready"]:
            return {
                "published": False,
                "deferred": True,
                "detail": str(github["detail"]),
            }
        self._scan_secrets(source_dir)
        account = self.settings.github_account or str(github["account"])
        repository = f"{account}/{safe_name}"
        executable = str(github["executable"])
        token = self._github_token(executable, account)
        if token is None:
            return {
                "published": False,
                "deferred": True,
                "repository": repository,
                "detail": f"GitHub credentials for {account!r} are unavailable.",
            }
        if (source_dir / ".git").is_dir():
            environment = self._safe_git_environment(
                self._github_environment(token, executable),
                source_dir,
            )
            remote = await self._run_process(
                [executable, "repo", "view", repository, "--json", "nameWithOwner"],
                cwd=source_dir,
                timeout=60,
                env=environment,
            )
            created_output = ""
            if remote["returncode"] != 0:
                created = await self._run_process(
                    [executable, "repo", "create", repository, f"--{selected_visibility}"],
                    cwd=source_dir,
                    timeout=120,
                    env=environment,
                )
                created_output = str(created["output_tail"])
                if created["returncode"] != 0:
                    return {
                        "published": False,
                        "deferred": False,
                        "repository": repository,
                        "url": f"https://github.com/{repository}",
                        "detail": created_output,
                    }
            existing = await self._publish_existing_directory(
                source_dir=source_dir,
                repository=repository,
                env=environment,
            )
            if existing["returncode"] == 0:
                return {
                    "published": True,
                    "deferred": False,
                    "repository": repository,
                    "url": f"https://github.com/{repository}",
                    "detail": "\n".join(
                        item for item in (created_output, existing["output_tail"]) if item
                    ),
                    "updated": True,
                }
            return {
                "published": False,
                "deferred": False,
                "repository": repository,
                "url": f"https://github.com/{repository}",
                "detail": existing["output_tail"],
            }
        self._initialize_git(source_dir)
        environment = self._safe_git_environment(
            self._github_environment(token, executable),
            source_dir,
        )
        result = await self._run_process(
            [
                executable,
                "repo",
                "create",
                repository,
                f"--{selected_visibility}",
                "--source",
                str(source_dir),
                "--remote",
                "origin",
                "--push",
            ],
            cwd=source_dir,
            timeout=180,
            env=environment,
        )
        published = result["returncode"] == 0
        return {
            "published": published,
            "deferred": False,
            "repository": repository,
            "url": f"https://github.com/{repository}",
            "detail": result["output_tail"],
        }

    async def _publish_existing_directory(
        self,
        *,
        source_dir: Path,
        repository: str,
        env: dict[str, str],
    ) -> dict[str, Any]:
        safe_directory = f"safe.directory={source_dir.resolve()}"
        remote_check = await self._run_process(
            ["git", "-c", safe_directory, "remote", "get-url", "origin"],
            cwd=source_dir,
            timeout=60,
            env=env,
        )
        remote_operation = "set-url" if remote_check["returncode"] == 0 else "add"
        commands = (
            [
                "git",
                "-c",
                safe_directory,
                "remote",
                remote_operation,
                "origin",
                f"https://github.com/{repository}.git",
            ],
            ["git", "-c", safe_directory, "add", "-A"],
            [
                "git",
                "-c",
                safe_directory,
                "-c",
                "user.name=ECK Autonomous Developer",
                "-c",
                "user.email=eck-local@users.noreply.github.com",
                "commit",
                "-m",
                "Improve verified ECK mission",
            ],
            [
                "git",
                "-c",
                safe_directory,
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "push",
                "origin",
                "main",
            ],
        )
        output: list[str] = [str(remote_check["output_tail"])]
        for index, command in enumerate(commands):
            result = await self._run_process(command, cwd=source_dir, timeout=180, env=env)
            output.append(str(result["output_tail"]))
            if result["returncode"] != 0:
                no_changes = index == 2 and "nothing to commit" in str(
                    result["output_tail"]
                ).casefold()
                if no_changes:
                    continue
                return {"returncode": result["returncode"], "output_tail": "\n".join(output)}
        return {"returncode": 0, "output_tail": "\n".join(output)[-8000:]}

    async def validate_python_directory(
        self,
        source_dir: Path,
        *,
        objective: str,
    ) -> dict[str, Any]:
        if not source_dir.is_dir():
            return {"success": False, "detail": "Python project source directory is missing."}
        self._scan_secrets(source_dir)
        quality = self._static_quality_gate(source_dir, objective=objective)
        if not quality["success"]:
            return {
                "success": False,
                "detail": "Static Python quality contract failed.",
                "output_tail": "; ".join(quality["issues"]),
                "quality": quality,
                "isolated": False,
            }
        validation = await self._validate_in_docker(source_dir)
        validation["quality"] = quality
        return validation

    def github_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._github_cache is not None
            and now - self._github_checked_at < 30
        ):
            return self._github_cache
        executable = self._gh_executable()
        if executable is None:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": False,
                "detail": "GitHub CLI is not installed.",
            })
        expected = self.settings.github_account
        token: str | None = None
        environment: dict[str, str] | None = None
        if expected:
            token = self._github_token(executable, expected)
            if token is None:
                return self._cache_github(now, {
                    "ready": False,
                    "authenticated": False,
                    "account": expected,
                    "executable": executable,
                    "detail": (
                        f"GitHub CLI has no stored OAuth credential for the dedicated "
                        f"ECK account {expected!r}."
                    ),
                })
            environment = self._github_environment(token, executable)
        try:
            GitHubCommandPolicy.validate([executable, "api", "user", "--jq", ".login"])
            account_result = subprocess.run(
                [executable, "api", "user", "--jq", ".login"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": True,
                "executable": executable,
                "detail": f"GitHub account lookup failed: {type(exc).__name__}: {exc}",
            })
        if account_result.returncode != 0:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": False,
                "account": expected,
                "executable": executable,
                "detail": "GitHub CLI is installed but the requested account is unavailable.",
            })
        account = account_result.stdout.strip()
        if not expected:
            return self._cache_github(now, {
                "ready": False,
                "authenticated": True,
                "account": account,
                "executable": executable,
                "detail": (
                    "GitHub CLI is authenticated, but the dedicated ECK account is not "
                    "configured. Set ECK_GITHUB_ACCOUNT before autonomous publication."
                ),
            })
        if expected and account.casefold() != expected.casefold():
            return self._cache_github(now, {
                "ready": False,
                "authenticated": True,
                "account": account,
                "executable": executable,
                "detail": f"Authenticated GitHub account is {account!r}, expected {expected!r}.",
            })
        return self._cache_github(now, {
            "ready": bool(account),
            "authenticated": True,
            "account": account,
            "executable": executable,
            "detail": (
                "GitHub publisher is ready."
                if account
                else "GitHub account was not resolved."
            ),
        })

    def _cache_github(self, checked_at: float, value: dict[str, Any]) -> dict[str, Any]:
        self._github_checked_at = checked_at
        self._github_cache = value
        return value

    @staticmethod
    def _github_token(executable: str, account: str) -> str | None:
        command = [
            executable,
            "auth",
            "token",
            "--hostname",
            "github.com",
            "--user",
            account,
        ]
        GitHubCommandPolicy.validate(command)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        token = result.stdout.strip()
        return token if result.returncode == 0 and token else None

    @staticmethod
    def _github_environment(token: str, executable: str | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("GITHUB_TOKEN", None)
        environment["GH_TOKEN"] = token
        environment["GH_HOST"] = "github.com"
        if executable:
            executable_dir = str(Path(executable).resolve().parent)
            environment["PATH"] = executable_dir + os.pathsep + environment.get("PATH", "")
        return environment

    @staticmethod
    def _safe_git_environment(environment: dict[str, str], source_dir: Path) -> dict[str, str]:
        value = dict(environment)
        value["GIT_CONFIG_COUNT"] = "1"
        value["GIT_CONFIG_KEY_0"] = "safe.directory"
        value["GIT_CONFIG_VALUE_0"] = str(source_dir.resolve())
        return value
