"""Back up the platform databases (data/state.db, data/checkpoints.db) with the SQLite online-backup API.

Usage:  uv run python scripts/backup_state.py
Copies go to data/backups/<UTC timestamp>/ (Working Plan §11: before any migration and before each part's
acceptance run). Missing databases are skipped.
"""

from __future__ import annotations

from devteam_core.config import load_settings
from devteam_state import backup_databases


def main() -> int:
    settings = load_settings()
    copies = backup_databases([settings.state_db_path, settings.checkpoints_db_path], settings.backups_dir)
    if not copies:
        print("nothing to back up: no platform database exists yet")
    for copy in copies:
        print(f"backed up -> {copy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
