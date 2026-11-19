"""Online backups of the platform SQLite databases (Working Plan §11, RV-14).

Uses the SQLite online-backup API, which produces a consistent copy even while other connections are open.
"""

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path


def backup_sqlite(source: Path, destination: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"backup target already exists: {destination}")
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return destination


def backup_databases(
    databases: Iterable[Path], backups_dir: Path, *, timestamp: datetime | None = None
) -> list[Path]:
    """Back up every existing database into ``<backups_dir>/<UTC timestamp>/``. Missing files are skipped."""
    stamp = (timestamp or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    target_dir = backups_dir / stamp
    return [backup_sqlite(db, target_dir / db.name) for db in databases if db.is_file()]
