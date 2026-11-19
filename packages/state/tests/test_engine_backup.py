import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from devteam_state import backup_databases, backup_sqlite, create_sqlite_engine


def test_engine_applies_pragmas(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "nested" / "state.db", busy_timeout_ms=1234)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 1234
    finally:
        engine.dispose()
    assert (tmp_path / "nested" / "state.db").is_file()


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "fk.db")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            conn.execute(
                text("CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent(id))")
            )
        with pytest.raises(Exception, match="FOREIGN KEY"), engine.begin() as conn:
            conn.execute(text("INSERT INTO child (id, pid) VALUES (1, 99)"))
    finally:
        engine.dispose()


def test_negative_busy_timeout_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        create_sqlite_engine(tmp_path / "x.db", busy_timeout_ms=-1)


def _make_db(path: Path, rows: int) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.close()


def test_backup_copies_content_while_source_is_open(tmp_path: Path) -> None:
    source = tmp_path / "state.db"
    _make_db(source, 25)
    holder = sqlite3.connect(source)  # an open connection must not prevent a consistent backup
    try:
        copy = backup_sqlite(source, tmp_path / "bk" / "state.db")
    finally:
        holder.close()
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone() == (25,)
    conn.close()


def test_backup_refuses_to_overwrite_and_requires_source(tmp_path: Path) -> None:
    source = tmp_path / "state.db"
    _make_db(source, 1)
    target = backup_sqlite(source, tmp_path / "bk" / "state.db")
    with pytest.raises(FileExistsError):
        backup_sqlite(source, target)
    with pytest.raises(FileNotFoundError):
        backup_sqlite(tmp_path / "missing.db", tmp_path / "bk2" / "m.db")


def test_backup_databases_skips_missing_and_uses_timestamp_dir(tmp_path: Path) -> None:
    present = tmp_path / "state.db"
    _make_db(present, 3)
    stamp = datetime(2026, 11, 19, 1, 2, 3, tzinfo=UTC)
    copies = backup_databases([present, tmp_path / "checkpoints.db"], tmp_path / "backups", timestamp=stamp)
    assert [p.name for p in copies] == ["state.db"]
    assert copies[0].parent.name == "20261119T010203000000Z"
