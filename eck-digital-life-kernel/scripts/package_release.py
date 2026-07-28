"""Create a deterministic source release ZIP and checksum manifest."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT.parent / "deliverables" / "eck-digital-life-kernel-v0.1.0.zip"
ROOT_NAME = "eck-digital-life-kernel"

EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "workspace",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_NAMES = {".coverage"}


def included_files() -> list[Path]:
    files = []
    for path in PROJECT_ROOT.rglob("*"):
        relative = path.relative_to(PROJECT_ROOT)
        if not path.is_file():
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(PROJECT_ROOT).as_posix())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_zip(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included_files():
            relative = path.relative_to(PROJECT_ROOT)
            archive_name = f"{ROOT_NAME}/{relative.as_posix()}"
            info = zipfile.ZipInfo(archive_name, date_time=(2026, 7, 29, 0, 0, 0))
            mode = path.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            archive.writestr(info, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    build_zip(output)
    digest = sha256(output)
    manifest = output.with_suffix(".sha256")
    manifest.write_text(f"{digest}  {output.name}{os.linesep}", encoding="utf-8")
    print(output)
    print(manifest)


if __name__ == "__main__":
    main()
