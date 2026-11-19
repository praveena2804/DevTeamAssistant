"""Alembic upgrade/downgrade on a temporary database (roadmap §13 Part A integration tests)."""

from pathlib import Path

import pytest
from sqlalchemy import inspect

from devteam_core.config import Settings
from devteam_state import (
    create_sqlite_engine,
    current_revision,
    downgrade,
    ensure_state_schema,
    head_revision,
    needs_upgrade,
    upgrade,
)

pytestmark = pytest.mark.integration


def _revision(db: Path) -> str | None:
    engine = create_sqlite_engine(db)
    try:
        return current_revision(engine)
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    assert needs_upgrade(db)
    upgrade(db)
    assert _revision(db) == head_revision() == "0001_baseline"
    assert not needs_upgrade(db)
    engine = create_sqlite_engine(db)
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    downgrade(db, "base")
    assert _revision(db) is None
    assert needs_upgrade(db)
    upgrade(db)  # re-applying after a downgrade works
    assert _revision(db) == head_revision()


def test_ensure_schema_creates_fresh_db_without_backup(isolated_settings: Settings) -> None:
    result = ensure_state_schema(isolated_settings)
    assert result.upgraded
    assert result.backups == []
    assert isolated_settings.state_db_path.is_file()
    assert ensure_state_schema(isolated_settings).upgraded is False


def test_ensure_schema_backs_up_existing_db_before_migrating(isolated_settings: Settings) -> None:
    db = isolated_settings.state_db_path
    upgrade(db)
    downgrade(db, "base")  # simulate a database that is behind the head revision
    result = ensure_state_schema(isolated_settings)
    assert result.upgraded
    assert [p.name for p in result.backups] == ["state.db"]
    assert result.backups[0].is_relative_to(isolated_settings.backups_dir)
    assert _revision(db) == head_revision()
