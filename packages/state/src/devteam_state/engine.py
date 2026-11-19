"""SQLite engine factory (Working Plan §11).

Every connection gets ``journal_mode=WAL``, ``foreign_keys=ON``, ``busy_timeout`` and ``synchronous=NORMAL``.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker


def sqlite_url(path: Path) -> URL:
    return URL.create("sqlite+pysqlite", database=str(path))


def _apply_pragmas(busy_timeout_ms: int) -> Any:
    def on_connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return on_connect


def create_sqlite_engine(path: Path, *, busy_timeout_ms: int = 5000) -> Engine:
    """Create an engine for a SQLite file, creating the parent directory if necessary."""
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be >= 0")
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(path),
        connect_args={"check_same_thread": False, "timeout": max(busy_timeout_ms, 1) / 1000},
    )
    event.listen(engine, "connect", _apply_pragmas(busy_timeout_ms))
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
