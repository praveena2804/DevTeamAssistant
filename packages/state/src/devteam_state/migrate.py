"""Programmatic Alembic helpers for ``data/state.db``."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from devteam_state.engine import create_sqlite_engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
VERSION_TABLE = "alembic_version"  # constant identifier, never user input


def alembic_config(db_path: Path, *, busy_timeout_ms: int = 5000) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("path_separator", "os")
    config.attributes["db_path"] = db_path
    config.attributes["busy_timeout_ms"] = busy_timeout_ms
    return config


def head_revision() -> str:
    script = ScriptDirectory.from_config(alembic_config(Path("unused.db")))
    head = script.get_current_head()
    if head is None:  # pragma: no cover - the baseline revision always exists
        raise RuntimeError("no Alembic head revision found")
    return head


def current_revision(engine: Engine) -> str | None:
    """Read the applied revision directly (cheap and silent; called by every ``/health`` probe)."""
    with engine.connect() as connection:
        if not inspect(connection).has_table(VERSION_TABLE):
            return None
        rows = connection.execute(text(f"SELECT version_num FROM {VERSION_TABLE}")).scalars().all()  # noqa: S608
    if len(rows) > 1:
        raise RuntimeError(f"multiple Alembic heads applied: {sorted(rows)}")
    return str(rows[0]) if rows else None


def upgrade(db_path: Path, revision: str = "head", *, busy_timeout_ms: int = 5000) -> None:
    command.upgrade(alembic_config(db_path, busy_timeout_ms=busy_timeout_ms), revision)


def downgrade(db_path: Path, revision: str, *, busy_timeout_ms: int = 5000) -> None:
    command.downgrade(alembic_config(db_path, busy_timeout_ms=busy_timeout_ms), revision)


def needs_upgrade(db_path: Path, *, busy_timeout_ms: int = 5000) -> bool:
    if not db_path.exists():
        return True
    engine = create_sqlite_engine(db_path, busy_timeout_ms=busy_timeout_ms)
    try:
        return current_revision(engine) != head_revision()
    finally:
        engine.dispose()
