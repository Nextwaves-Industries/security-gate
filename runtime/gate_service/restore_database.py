"""Offline command-line restore for a gate SQLite database.

Run this module from the release image only after stopping the gate service::

    python -m gate_service.restore_database \
      --backup /var/lib/nextwaves/state/db-backups/rfid_portal.pre-migration....db \
      --confirm RESTORE:rfid_portal.db
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .persistence import (
    DEFAULT_BACKUP_RETENTION,
    DatabasePersistenceError,
    resolve_database_path,
    restore_database_backup,
)


def _retention(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("retention must be an integer") from exc
    if not 1 <= result <= 100:
        raise argparse.ArgumentTypeError("retention must be between 1 and 100")
    return result


def build_parser() -> argparse.ArgumentParser:
    data_dir = Path(os.getenv("RFID_PORTAL_DATA_DIR", "/var/lib/nextwaves"))
    parser = argparse.ArgumentParser(
        description="Restore a verified SQLite backup while gate-service is stopped."
    )
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=data_dir)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--retention",
        type=_retention,
        default=DEFAULT_BACKUP_RETENTION,
        help="number of pre-restore safety backups to retain (default: 10)",
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help="required literal RESTORE:<database filename>",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.resolve()
    config_path = (args.config or data_dir / "config.json").resolve()
    database_path = resolve_database_path(data_dir, config_path).resolve()
    expected_confirmation = f"RESTORE:{database_path.name}"
    if args.confirm != expected_confirmation:
        print(
            f"Refusing restore: --confirm must equal {expected_confirmation}",
            file=sys.stderr,
        )
        return 2

    try:
        result = restore_database_backup(
            args.backup,
            database_path,
            retention=args.retention,
        )
    except DatabasePersistenceError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print(f"Restored database: {result.database_path}")
    if result.safety_backup is not None:
        print(f"Previous database backup: {result.safety_backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
