"""C.4 tables (migration 0004): approvals, secret references, usage counters, trace spans."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.statuses import Stage
from devteam_core.ids import new_id
from devteam_state import create_sqlite_engine, downgrade, session_factory, upgrade
from devteam_state.models import (
    ApprovalRow,
    Principal,
    Project,
    SecretReferenceRow,
    TraceSpanRow,
    UsageCounter,
)
from devteam_state.repositories import runs
from devteam_state.repositories.execution import (
    SpanRecord,
    Usage,
    add_secret_reference,
    add_spans,
    add_usage,
    approval_decision_key,
    get_usage,
    list_secret_references,
    spans_for_trace,
    usage_key,
)

NEW_TABLES = (ApprovalRow, SecretReferenceRow, UsageCounter, TraceSpanRow)


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


@pytest.fixture
def ids(sessions: sessionmaker[Session]) -> dict[str, str]:
    now = datetime.now(UTC)
    out = {"owner": new_id("prn"), "a": new_id("prj"), "b": new_id("prj")}
    with sessions.begin() as s:
        s.add(Principal(id=out["owner"], kind="human", display_name="O", created_at=now))
        for key in ("a", "b"):
            s.add(
                Project(
                    id=out[key],
                    name=key,
                    name_key=key,
                    description="",
                    status="active",
                    created_by=out["owner"],
                    created_at=now,
                    updated_at=now,
                )
            )
        s.flush()
        out["run"] = runs.create_run(s, project_id=out["a"], run_kind="initial", stage=Stage.BUILD).id
    return out


def test_migration_matches_the_models(db: Path) -> None:
    engine = create_sqlite_engine(db)
    try:
        inspector = inspect(engine)
        for model in NEW_TABLES:
            table = model.__table__
            assert isinstance(table, Table)
            live = {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)}
            assert live == {c.name: c.nullable for c in table.columns}, table.name
        assert "value" not in {c["name"] for c in inspector.get_columns("secret_references")}
        triggers = {
            r[0]
            for r in engine.connect().exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='approvals'"
            )
        }
        assert triggers == {"approvals_no_update", "approvals_no_delete"}
    finally:
        engine.dispose()


def test_downgrade_removes_only_the_c4_tables(db: Path) -> None:
    downgrade(db, "0003_harness_core")
    engine = create_sqlite_engine(db)
    try:
        names = set(inspect(engine).get_table_names())
        assert not names & {m.__tablename__ for m in NEW_TABLES}
        assert {"runs", "agent_tasks", "audit_events"} <= names
    finally:
        engine.dispose()
    upgrade(db)


def test_usage_accumulates_per_task_and_per_run(sessions: sessionmaker[Session], ids: dict[str, str]) -> None:
    with sessions.begin() as s:
        add_usage(
            s,
            project_id=ids["a"],
            run_id=ids["run"],
            task_id="tsk_1",
            delta=Usage(model_calls=1, input_tokens=5),
        )
        add_usage(
            s,
            project_id=ids["a"],
            run_id=ids["run"],
            task_id="tsk_1",
            delta=Usage(model_calls=1, output_tokens=7),
        )
        add_usage(s, project_id=ids["a"], run_id=ids["run"], task_id=None, delta=Usage(tool_calls=3))
    with sessions() as s:
        task = get_usage(s, ids["a"], ids["run"], "tsk_1")
        assert (task.model_calls, task.tokens) == (2, 12)
        assert get_usage(s, ids["a"], ids["run"]).tool_calls == 3
        assert get_usage(s, ids["b"], ids["run"], "tsk_1") == Usage()  # scoped by project
    assert usage_key("run_x", None) == "run_x:run:-" and usage_key("run_x", "tsk_1") == "run_x:task:tsk_1"


def test_usage_never_shrinks_and_never_crosses_projects(
    sessions: sessionmaker[Session], ids: dict[str, str]
) -> None:
    with sessions.begin() as s, pytest.raises(ValueError, match="only ever grows"):
        add_usage(s, project_id=ids["a"], run_id=ids["run"], task_id=None, delta=Usage(model_calls=-1))
    with sessions.begin() as s:
        add_usage(s, project_id=ids["a"], run_id=ids["run"], task_id=None, delta=Usage(model_calls=1))
    with sessions.begin() as s, pytest.raises(ValueError, match="another project"):
        add_usage(s, project_id=ids["b"], run_id=ids["run"], task_id=None, delta=Usage(model_calls=1))


def test_secret_references_hold_pointers_only(sessions: sessionmaker[Session], ids: dict[str, str]) -> None:
    with sessions.begin() as s:
        add_secret_reference(s, name="api_token", ref="secret://platform/api_token")
        add_secret_reference(s, name="github", ref=f"secret://{ids['a']}/github", project_id=ids["a"])
    with sessions() as s:
        assert [r.ref for r in list_secret_references(s, None)] == ["secret://platform/api_token"]
        assert [r.name for r in list_secret_references(s, ids["a"])] == ["github"]
        assert list_secret_references(s, ids["b"]) == []


def test_spans_are_stored_with_their_duration(sessions: sessionmaker[Session]) -> None:
    start = datetime(2026, 11, 20, tzinfo=UTC)
    record = SpanRecord(
        span_id="a" * 16,
        trace_id="b" * 32,
        parent_span_id=None,
        name="agent.execute",
        project_id=None,
        start_ts=start,
        end_ts=start + timedelta(milliseconds=1500),
        status="ok",
        attributes={"task_id": "tsk_1"},
    )
    with sessions.begin() as s:
        assert add_spans(s, [record]) == 1
    with sessions() as s:
        [row] = spans_for_trace(s, "b" * 32)
        assert (row.duration_ms, row.attributes) == (1500, {"task_id": "tsk_1"})


def test_decision_key_distinguishes_spec_versions() -> None:
    assert approval_decision_key("run_1", "spec_approval", None) == "run_1:spec_approval:-"
    assert approval_decision_key("run_1", "spec_approval", "spv_2") == "run_1:spec_approval:spv_2"
