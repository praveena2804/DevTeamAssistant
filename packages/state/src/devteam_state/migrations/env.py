"""Alembic environment for ``data/state.db``.

The database path comes from ``config.attributes['db_path']`` (programmatic use, see
``devteam_state.migrate``) or, for CLI use, from the validated platform configuration. It is never read
from alembic.ini.
"""

from pathlib import Path

from alembic import context

from devteam_state.engine import create_sqlite_engine
from devteam_state.models import Base

config = context.config
target_metadata = Base.metadata


def _db_path() -> tuple[Path, int]:
    db_path = config.attributes.get("db_path")
    if db_path is not None:
        return Path(db_path), int(config.attributes.get("busy_timeout_ms", 5000))
    from devteam_core.config import load_settings  # noqa: PLC0415 - CLI path only

    settings = load_settings()
    return settings.state_db_path, settings.database.busy_timeout_ms


def run_migrations_online() -> None:
    db_path, busy_timeout_ms = _db_path()
    engine = create_sqlite_engine(db_path, busy_timeout_ms=busy_timeout_ms)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,  # SQLite needs batch mode for ALTER operations
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) migrations are not supported; run migrations online")
run_migrations_online()
