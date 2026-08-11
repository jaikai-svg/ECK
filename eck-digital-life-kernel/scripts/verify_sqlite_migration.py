from __future__ import annotations

import argparse
import json
from pathlib import Path

from eck.storage.migration import SQLiteMigrationVerifier
from eck.storage.sqlite import SQLiteStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify ECK SQLite upgrade, preservation, and rollback on copies.",
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--allow-live-source",
        action="store_true",
        help="Allow the running ECK process to append data after the hot backup is captured.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verifier = SQLiteMigrationVerifier(args.database)
    report = verifier.verify(
        lambda path: SQLiteStore(path).initialize(),
        output_dir=args.output_dir,
        require_source_unchanged=not args.allow_live_source,
    )
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
