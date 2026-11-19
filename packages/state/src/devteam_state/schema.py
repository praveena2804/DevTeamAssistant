"""Startup schema management: back up an existing database before any migration, then upgrade."""

from dataclasses import dataclass, field
from pathlib import Path

from devteam_core.config import Settings
from devteam_core.logging import get_logger
from devteam_state.backup import backup_databases
from devteam_state.migrate import head_revision, needs_upgrade, upgrade

_log = get_logger(__name__)


@dataclass(frozen=True)
class SchemaResult:
    upgraded: bool
    revision: str
    backups: list[Path] = field(default_factory=list)


def ensure_state_schema(settings: Settings) -> SchemaResult:
    """Bring ``data/state.db`` to the head revision.

    An existing database is backed up first (Working Plan §11: "before any migration"). A brand-new
    database has nothing to back up.
    """
    db_path = settings.state_db_path
    busy = settings.database.busy_timeout_ms
    if not needs_upgrade(db_path, busy_timeout_ms=busy):
        return SchemaResult(upgraded=False, revision=head_revision())
    backups: list[Path] = []
    if db_path.exists():
        backups = backup_databases([db_path, settings.checkpoints_db_path], settings.backups_dir)
        _log.info("state_db_backed_up", files=[str(p) for p in backups])
    upgrade(db_path, busy_timeout_ms=busy)
    revision = head_revision()
    _log.info("state_db_migrated", revision=revision, db_path=str(db_path))
    return SchemaResult(upgraded=True, revision=revision, backups=backups)
