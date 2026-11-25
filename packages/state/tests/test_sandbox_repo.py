"""Part E tables (migration 0007): schema, triggers, downgrade safety and repositories."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import FixedClock
from devteam_core.contracts.sandbox import (
    ContainerLimits,
    RecipeOutcome,
    SandboxExecution,
    SandboxExecutionStatus,
    TemplateVerification,
    TemplateVerificationStatus,
    TestCounts,
)
from devteam_core.ids import new_id
from devteam_state import create_sqlite_engine, downgrade, session_factory, upgrade
from devteam_state.repositories import sandbox as repo

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
LIMITS = ContainerLimits(
    memory_mb=1536, cpus=1.5, pids=256, wall_clock_s=600, tmpfs_mb=256, work_copy_mb=512, output_kb=1024
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    upgrade(path)
    return path


@pytest.fixture
def engine_sessions(db: Path) -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    engine = create_sqlite_engine(db)
    yield engine, session_factory(engine)
    engine.dispose()


@pytest.fixture
def sessions(engine_sessions: tuple[Engine, sessionmaker[Session]]) -> sessionmaker[Session]:
    return engine_sessions[1]


def _queued(template_id: str = "react_fastapi_sqlite", at: datetime = T0) -> TemplateVerification:
    return TemplateVerification(
        verification_id=new_id("tvf"),
        template_id=template_id,
        template_version="1.0.0",
        status=TemplateVerificationStatus.QUEUED,
        requested_by="owner",
        requested_at=at,
    )


def _passed(base: TemplateVerification) -> TemplateVerification:
    outcome = RecipeOutcome(
        recipe="pytest_api",
        status=SandboxExecutionStatus.COMPLETED,
        exit_code=0,
        reports_tests=True,
        tests=TestCounts(tests=3, passed=3, failed=0, errors=0, skipped=0),
    )
    return base.model_copy(
        update={
            "status": TemplateVerificationStatus.PASSED,
            "recipes": (outcome,),
            "template_checksum": "b" * 64,
            "image_digests": {"python_api": DIGEST},
            "finished_at": T0 + timedelta(minutes=2),
            "duration_ms": 120_000,
        }
    )


def test_tables_exist_and_downgrade_removes_only_them(db: Path) -> None:
    downgrade(db, "0007_sandbox")  # later migrations (Part F) first, so only 0007's tables are compared
    engine = create_sqlite_engine(db)
    names = set(inspect(engine).get_table_names())
    assert {"sandbox_executions", "template_verifications", "resource_lock"} <= names
    engine.dispose()
    downgrade(db, "0006_agent_outputs")
    engine = create_sqlite_engine(db)
    after = set(inspect(engine).get_table_names())
    engine.dispose()
    assert after == names - {"sandbox_executions", "template_verifications", "resource_lock"}
    upgrade(db)


def test_resource_lock_single_holder_takeover_and_release(sessions: sessionmaker[Session]) -> None:
    clock = FixedClock(T0)
    with sessions.begin() as s:
        assert repo.acquire_resource_lock(s, holder="llm", owner="a", pid=1, ttl_s=30, clock=clock).acquired
    with sessions.begin() as s:
        result = repo.acquire_resource_lock(s, holder="sandbox", owner="b", pid=2, ttl_s=30, clock=clock)
        assert not result.acquired and result.held_by == "llm"
    clock.set(T0 + timedelta(seconds=31))  # holder "a" stopped heart-beating
    with sessions.begin() as s:
        result = repo.acquire_resource_lock(s, holder="sandbox", owner="b", pid=2, ttl_s=30, clock=clock)
        assert result.acquired and result.taken_over_from == "a"
        assert not repo.renew_resource_lock(s, owner="a", clock=clock)
        assert not repo.release_resource_lock(s, owner="a")
        assert repo.release_resource_lock(s, owner="b")
        assert repo.current_resource_lock(s) is None


def test_resource_lock_has_one_row_and_known_holders(
    engine_sessions: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine, _ = engine_sessions
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO resource_lock VALUES (1,'llm','x',1,'2026-01-01','2026-01-01')"))
    for row in ("(2,'llm','y',1,'2026-01-01','2026-01-01')", "(1,'gpu','z',1,'2026-01-01','2026-01-01')"):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(f"INSERT INTO resource_lock VALUES {row}"))


def test_verification_claim_finish_and_stale(sessions: sessionmaker[Session]) -> None:
    first, second = _queued(at=T0), _queued(at=T0 + timedelta(seconds=1))
    with sessions.begin() as s:
        repo.add_verification(s, first)
        repo.add_verification(s, second)
        assert repo.has_active_verification(s, "react_fastapi_sqlite")
    with sessions.begin() as s:
        claimed = repo.claim_next_verification(s, clock=FixedClock(T0))
        assert claimed is not None and claimed.verification_id == first.verification_id
        assert claimed.status is TemplateVerificationStatus.RUNNING
    with sessions.begin() as s:
        repo.finish_verification(s, _passed(first))
    with sessions.begin() as s:
        stored = repo.get_verification(s, first.verification_id)
        assert stored is not None and stored.status is TemplateVerificationStatus.PASSED
        assert (
            repo.mark_stale(
                s,
                template_id="react_fastapi_sqlite",
                current_checksum="b" * 64,
                current_digests={"python_api": DIGEST},
            )
            == 0
        )
        assert (
            repo.mark_stale(
                s,
                template_id="react_fastapi_sqlite",
                current_checksum="c" * 64,
                current_digests={"python_api": DIGEST},
            )
            == 1
        )
        latest = repo.latest_verifications(s)
        assert latest["react_fastapi_sqlite"].verification_id == second.verification_id


def test_status_only_moves_forward_and_final_results_are_frozen(
    engine_sessions: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine, sessions = engine_sessions
    item = _queued()
    with sessions.begin() as s:
        repo.add_verification(s, item)
    with pytest.raises(DatabaseError), engine.begin() as conn:  # queued -> passed skips running
        conn.execute(
            text("UPDATE template_verifications SET status='passed' WHERE id=:i"), {"i": item.verification_id}
        )
    with sessions.begin() as s:
        repo.claim_next_verification(s)
    with sessions.begin() as s:
        repo.finish_verification(s, _passed(item))
    for statement in (
        "UPDATE template_verifications SET status='running' WHERE id=:i",
        "UPDATE template_verifications SET reason='edited' WHERE id=:i",
        "UPDATE template_verifications SET status='stale', recipes='[]' WHERE id=:i",
    ):
        with pytest.raises(DatabaseError), engine.begin() as conn:
            conn.execute(text(statement), {"i": item.verification_id})


def test_executions_are_append_only_and_owned_once(
    engine_sessions: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine, sessions = engine_sessions
    item = _queued()
    with sessions.begin() as s:
        repo.add_verification(s, item)
    execution = SandboxExecution(
        execution_id=new_id("sbx"),
        verification_id=item.verification_id,
        recipe="pytest_api",
        image="devteam-sandbox/python_api",
        image_digest=DIGEST,
        limits=LIMITS,
        status=SandboxExecutionStatus.FAILED,
        exit_code=1,
        duration_ms=10,
        reason="tests failed",
        started_at=T0,
        finished_at=T0 + timedelta(seconds=1),
    )
    with sessions.begin() as s:
        repo.record_execution(s, execution)
    with sessions() as s:
        assert repo.executions_for_verification(s, item.verification_id) == [execution]
    with pytest.raises(DatabaseError), engine.begin() as conn:
        conn.execute(text("UPDATE sandbox_executions SET status='completed'"))
    with pytest.raises(IntegrityError), engine.begin() as conn:  # neither project nor verification
        conn.execute(
            text(
                "INSERT INTO sandbox_executions (id, recipe, status, payload, started_at, finished_at) "
                "VALUES ('sbx_x','pytest_api','failed','{}','2026-01-01','2026-01-01')"
            )
        )
