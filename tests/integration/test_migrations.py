"""Alembic upgrade/downgrade on a temporary database (roadmap §13 Part A integration tests)."""

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError

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

PART_B_TABLES = {
    "principals",
    "principal_roles",
    "projects",
    "source_documents",
    "document_chunks",
    "audit_events",
}


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
    assert _revision(db) == head_revision() == "0007_sandbox"
    assert not needs_upgrade(db)
    engine = create_sqlite_engine(db)
    try:
        assert set(inspect(engine).get_table_names()) >= {"alembic_version", *PART_B_TABLES}
    finally:
        engine.dispose()
    downgrade(db, "0001_baseline")
    engine = create_sqlite_engine(db)
    try:
        assert not set(inspect(engine).get_table_names()) & PART_B_TABLES
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


@pytest.mark.security
def test_audit_events_are_append_only_at_database_level(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_events (id, ts, action, outcome, details) "
                    "VALUES ('aud_1', '2026-11-19T00:00:00+00:00', 'x', 'success', '{}')"
                )
            )
        for statement in ("UPDATE audit_events SET action = 'y'", "DELETE FROM audit_events"):
            with pytest.raises(DatabaseError, match="append-only"), engine.begin() as conn:
                conn.execute(text(statement))
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT action FROM audit_events WHERE id = 'aud_1'")).scalar_one() == "x"
            )
    finally:
        engine.dispose()
