"""Check the audit log's hash chain (HC-21): ``uv run python scripts/verify_audit.py``.

Recomputes every chained entry and the digest over the entries written before the chain existed (Part B),
and prints whether the log is intact or the first entry where it is broken. Exit code 0 = intact,
1 = broken. Read-only: nothing is written.
"""

from __future__ import annotations

from devteam_core.config import load_settings
from devteam_state import create_sqlite_engine, session_factory
from devteam_state.repositories.audit import ChainReport, verify_chain


def check() -> ChainReport:
    settings = load_settings()
    engine = create_sqlite_engine(settings.state_db_path, busy_timeout_ms=settings.database.busy_timeout_ms)
    try:
        with session_factory(engine)() as session:
            return verify_chain(session)
    finally:
        engine.dispose()


def main() -> int:
    report = check()
    if report.ok:
        print(
            f"Audit log intact: {report.checked} chained entries and {report.pre_chain_rows} earlier entries "
            "verified."
        )
        return 0
    print(f"Audit log BROKEN at entry {report.broken_at}: {report.reason}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
