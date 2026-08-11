from __future__ import annotations

from pathlib import Path


class GitHubCommandPolicy:
    """Fail-closed command boundary for autonomous GitHub operations."""

    @classmethod
    def validate(cls, command: list[str]) -> None:
        if not command or Path(command[0]).stem.casefold() != "gh":
            return
        arguments = command[1:]
        if cls._auth_token(arguments) or cls._read_current_user(arguments):
            return
        if len(arguments) >= 2 and arguments[:2] in (
            ["repo", "create"],
            ["repo", "view"],
        ):
            return
        raise RuntimeError(
            "Autonomous GitHub command blocked by the permanent account-safety allowlist."
        )

    @staticmethod
    def _auth_token(arguments: list[str]) -> bool:
        return (
            len(arguments) == 6
            and arguments[:2] == ["auth", "token"]
            and arguments[2] == "--hostname"
            and arguments[3] == "github.com"
            and arguments[4] == "--user"
            and bool(arguments[5])
        )

    @staticmethod
    def _read_current_user(arguments: list[str]) -> bool:
        return arguments == ["api", "user", "--jq", ".login"]
