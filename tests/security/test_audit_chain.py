"""C.3 hash-chained audit (HC-21, HS-11): tamper detection, Part B rows covered, concurrency.

Tampering is simulated the way an attacker with file access would do it: open the SQLite file directly,
drop the append-only triggers, and edit, delete, insert or reorder rows. ``verify_chain`` must report the
first broken entry. The untampered baseline must verify as intact.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.audit_chain import ANCHOR_ACTION
from devteam_state.models import AuditEvent
from devteam_state.repositories import audit

pytestmark = pytest.mark.security


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    upgrade(path)
    return path


@pytest.fixture
def sessions(db: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


def _write(sessions: sessionmaker[Session], count: int = 5) -> None:
    for i in range(count):
        with sessions.begin() as s:
            audit.append(
                s,
                action=f"test.event{i}",
                outcome="success",
                principal_id="service:test",
                details={"n": i, "nested": {"k": [1, 2]}},
            )


def _verify(sessions: sessionmaker[Session]) -> audit.ChainReport:
    with sessions() as s:
        return audit.verify_chain(s)


def _tamper(db: Path, *statements: str) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        con.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
        con.execute("DROP TRIGGER IF EXISTS audit_chain_head_forward_only")
        for statement in statements:
            con.execute(statement)
        con.commit()
    finally:
        con.close()


def test_fresh_database_has_an_anchor_and_verifies(sessions: sessionmaker[Session]) -> None:
    report = _verify(sessions)
    assert report.ok and report.checked == 1 and report.pre_chain_rows == 0
    with sessions() as s:
        anchor = s.scalars(select(AuditEvent).where(AuditEvent.seq == 1)).one()
    assert anchor.action == ANCHOR_ACTION


def test_appended_events_form_an_intact_chain(sessions: sessionmaker[Session]) -> None:
    _write(sessions, 10)
    report = _verify(sessions)
    assert report.ok and report.checked == 11 and report.broken_at is None


def test_redacted_details_are_what_gets_hashed(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as s:
        audit.append(s, action="x", outcome="success", details={"password": "hunter2-secret"})
    assert _verify(sessions).ok
    with sessions() as s:
        row = s.scalars(select(AuditEvent).where(AuditEvent.action == "x")).one()
    assert "hunter2-secret" not in str(row.details)


@pytest.mark.parametrize(
    ("statements", "broken_at", "reason"),
    [
        (("UPDATE audit_events SET details = '{\"n\": 99}' WHERE seq = 3",), 3, "altered"),
        (("UPDATE audit_events SET outcome = 'denied' WHERE seq = 4",), 4, "altered"),
        (("UPDATE audit_events SET principal_id = 'prn_FORGED' WHERE seq = 2",), 2, "altered"),
        (("UPDATE audit_events SET ts = '2020-01-01 00:00:00.000000' WHERE seq = 5",), 5, "altered"),
        (("DELETE FROM audit_events WHERE seq = 3",), 3, "missing"),
        (
            (
                "UPDATE audit_events SET seq = 100 WHERE seq = 3",
                "UPDATE audit_events SET seq = 3 WHERE seq = 4",
                "UPDATE audit_events SET seq = 4 WHERE seq = 100",
            ),
            3,
            "link",
        ),
        (("DELETE FROM audit_events WHERE seq = 6", "UPDATE audit_chain_head SET seq = 6"), 6, "removed"),
        (("DELETE FROM audit_events WHERE seq = 6",), 6, "removed"),  # the newest entry deleted
        (("UPDATE audit_events SET hash = 'f' || substr(hash, 2) WHERE seq = 6",), 6, "altered"),
    ],
)
def test_tampering_is_detected_at_the_right_entry(
    db: Path, sessions: sessionmaker[Session], statements: tuple[str, ...], broken_at: int, reason: str
) -> None:
    _write(sessions, 5)  # seq 2..6
    assert _verify(sessions).ok
    _tamper(db, *statements)
    report = _verify(sessions)
    assert not report.ok
    assert report.broken_at == broken_at, report
    assert reason in (report.reason or "")


def test_forged_insert_with_recomputed_hash_breaks_the_next_link(
    db: Path, sessions: sessionmaker[Session]
) -> None:
    """An attacker who inserts a row and fixes its own hash still breaks the following entry."""
    _write(sessions, 3)
    with sessions() as s:
        third = s.scalars(select(AuditEvent).where(AuditEvent.seq == 3)).one()
        prev = third.prev_hash
    forged = AuditEvent(
        id="aud_forged",
        ts=datetime.now(UTC),
        principal_id="prn_x",
        action="approval.recorded",
        project_id=None,
        target_type=None,
        target_id=None,
        outcome="success",
        request_id=None,
        trace_id=None,
        details={},
    )
    from devteam_state.audit_chain import link_hash  # noqa: PLC0415

    forged_hash = link_hash(prev or "", forged, 3)
    _tamper(
        db,
        "UPDATE audit_events SET seq = seq + 1000 WHERE seq >= 3",
        "UPDATE audit_events SET seq = seq - 999 WHERE seq >= 1000",
        f"INSERT INTO audit_events (id, ts, principal_id, action, outcome, details, seq, prev_hash, hash) VALUES "  # noqa: E501
        f"('aud_forged', '{forged.ts.replace(tzinfo=None).isoformat(sep=' ')}', 'prn_x', 'approval.recorded', "  # noqa: E501
        f"'success', '{{}}', 3, '{prev}', '{forged_hash}')",
    )
    report = _verify(sessions)
    assert not report.ok and report.broken_at == 4 and "link" in (report.reason or "")


def test_known_limit_tail_truncation_with_a_rewound_head(db: Path, sessions: sessionmaker[Session]) -> None:
    """Documented limit: an attacker with file access who drops the triggers, deletes the newest entries
    AND rewinds the head to match is not detectable from inside the same file. The forward-only trigger
    blocks the rewind for anyone who does not drop triggers; an external copy of the head hash (planned
    with run history, Part D) closes this gap."""
    _write(sessions, 3)
    with sessions() as s:
        fourth = s.scalars(select(AuditEvent).where(AuditEvent.seq == 3)).one()
    _tamper(
        db,
        "DELETE FROM audit_events WHERE seq = 4",
        f"UPDATE audit_chain_head SET seq = 3, hash = '{fourth.hash}'",
    )
    assert _verify(sessions).ok  # recorded honestly as a limitation (KI-019)


def test_triggers_still_block_casual_edits(db: Path, sessions: sessionmaker[Session]) -> None:
    _write(sessions, 1)
    engine = create_sqlite_engine(db)
    try:
        for statement in (
            "UPDATE audit_events SET action = 'y'",
            "DELETE FROM audit_events",
            "DELETE FROM audit_chain_head",
            "UPDATE audit_chain_head SET seq = 0",
        ):
            with pytest.raises(DatabaseError), engine.begin() as conn:
                conn.execute(text(statement))
    finally:
        engine.dispose()
    assert _verify(sessions).ok


# ------------------------------------------------------------------- Part B rows (pre-chain anchor)


def _part_b_database(tmp_path: Path) -> Path:
    path = tmp_path / "partb.db"
    upgrade(path, "0002_intake_iam_audit")
    con = sqlite3.connect(path)
    for i in range(3):
        con.execute(
            "INSERT INTO audit_events (id, ts, principal_id, action, outcome, details) VALUES "
            f"('aud_b{i}', '2026-11-19 10:0{i}:00.000000', 'prn_owner', 'project.create', 'success', '{{\"i\": {i}}}')"  # noqa: E501
        )
    con.commit()
    con.close()
    return path


def test_upgrade_keeps_part_b_rows_untouched_and_covers_them(tmp_path: Path) -> None:
    path = _part_b_database(tmp_path)
    before = sqlite3.connect(path).execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    upgrade(path)
    con = sqlite3.connect(path)
    after = con.execute(
        "SELECT id, ts, principal_id, action, project_id, target_type, target_id, outcome, request_id, trace_id,"  # noqa: E501
        " details FROM audit_events WHERE seq IS NULL ORDER BY id"
    ).fetchall()
    con.close()
    assert after == before  # not a single byte of the Part B rows changed
    engine = create_sqlite_engine(path)
    try:
        report = _verify(session_factory(engine))
    finally:
        engine.dispose()
    assert report.ok and report.pre_chain_rows == 3


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'project.delete' WHERE id = 'aud_b1'",
        "DELETE FROM audit_events WHERE id = 'aud_b0'",
        "INSERT INTO audit_events (id, ts, action, outcome, details) VALUES ('aud_late', '2026-11-19 09:00:00', 'x', 'success', '{}')",  # noqa: E501
    ],
)
def test_tampering_with_part_b_rows_is_detected(tmp_path: Path, statement: str) -> None:
    path = _part_b_database(tmp_path)
    upgrade(path)
    _tamper(path, statement)
    engine = create_sqlite_engine(path)
    try:
        report = _verify(session_factory(engine))
    finally:
        engine.dispose()
    assert not report.ok and report.broken_at == 1 and "before the chain" in (report.reason or "")


# ------------------------------------------------------------------------------------ concurrency


def test_concurrent_appenders_keep_one_linear_chain(db: Path) -> None:
    engine = create_sqlite_engine(db, busy_timeout_ms=10_000)
    factory = session_factory(engine)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(10):
                with factory.begin() as s:
                    audit.append(s, action=f"w{n}.{i}", outcome="success")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    try:
        assert errors == []
        report = _verify(factory)
        assert report.ok and report.checked == 61
    finally:
        engine.dispose()


def test_append_without_migrated_chain_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    upgrade(path, "0002_intake_iam_audit")
    engine = create_sqlite_engine(path)
    try:
        with pytest.raises(Exception, match=r"chain|no such"), session_factory(engine).begin() as s:
            audit.append(s, action="x", outcome="success")
    finally:
        engine.dispose()
