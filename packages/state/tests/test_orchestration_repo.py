"""D.1 orchestration tables (migration 0005): schema, triggers, downgrade safety and repositories."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, Table, inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import FixedClock
from devteam_core.contracts.events import GateOpened
from devteam_core.contracts.orchestration import attempt_key
from devteam_core.contracts.statuses import AgentRole, GateName, Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_state import create_sqlite_engine, downgrade, session_factory, upgrade
from devteam_state.models import (
    NodeEffect,
    Principal,
    Project,
    Run,
    RunEventRow,
    RunJob,
    SpecVersionRow,
    TaskPlanRow,
    WorkerLease,
)
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import runs
from devteam_state.repositories import spec_versions as specs

NEW_TABLES = (SpecVersionRow, TaskPlanRow, RunJob, WorkerLease, RunEventRow, NodeEffect)
T0 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


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


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def ids(sessions: sessionmaker[Session]) -> dict[str, str]:
    out = {"owner": new_id("prn"), "a": new_id("prj"), "b": new_id("prj")}
    with sessions.begin() as s:
        s.add(Principal(id=out["owner"], kind="human", display_name="O", created_at=T0))
        for key in ("a", "b"):
            s.add(
                Project(
                    id=out[key],
                    name=key,
                    name_key=key,
                    description="",
                    status="active",
                    created_by=out["owner"],
                    created_at=T0,
                    updated_at=T0,
                )
            )
        s.flush()
        out["run"] = runs.create_run(s, project_id=out["a"], run_kind="initial", stage=Stage.INTAKE).id
        out["run_b"] = runs.create_run(s, project_id=out["b"], run_kind="initial", stage=Stage.INTAKE).id
    return out


def _triggers(db: Path, table: str) -> set[str]:
    engine = create_sqlite_engine(db)
    try:
        with engine.connect() as conn:
            return {
                r[0]
                for r in conn.exec_driver_sql(
                    f"SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='{table}'"
                )
            }
    finally:
        engine.dispose()


# ----------------------------------------------------------------------------------- schema


def test_migration_matches_the_models(db: Path) -> None:
    engine = create_sqlite_engine(db)
    try:
        inspector = inspect(engine)
        for model in (*NEW_TABLES, Run):
            table = model.__table__
            assert isinstance(table, Table)
            live = {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)}
            assert live == {c.name: c.nullable for c in table.columns}, table.name
    finally:
        engine.dispose()
    assert _triggers(db, "spec_versions") == {
        "spec_versions_insert_draft_only",
        "spec_versions_guard_update",
        "spec_versions_no_delete",
    }
    assert _triggers(db, "node_effects") == {"node_effects_forward_only"}
    assert _triggers(db, "run_events") == {"run_events_no_update"}
    assert _triggers(db, "task_plans") == {"task_plans_no_update"}


def test_new_run_columns_have_safe_defaults(sessions: sessionmaker[Session], ids: dict[str, str]) -> None:
    with sessions() as s:
        run = s.get(Run, ids["run"])
        assert run is not None
        assert (run.mode, run.control_request, run.current_node, run.pending_gate) == (
            "stub",
            "none",
            None,
            None,
        )


@pytest.mark.security
def test_downgrade_keeps_runs_and_their_child_rows(
    db: Path, sessions: sessionmaker[Session], ids: dict[str, str]
) -> None:
    """The downgrade must not rebuild ``runs`` (a rebuild would cascade-delete child rows)."""
    task = AgentTask(
        task_id=new_id("tsk"),
        project_id=ids["a"],
        run_id=ids["run"],
        agent_type=AgentRole.BRD_ANALYST,
        operation_id="brd_analyst.extract_section",
        objective="analyse",
        done_criteria=("done",),
    )
    with sessions.begin() as s:
        task_id = runs.add_task(s, task).id
    downgrade(db, "0004_execution")
    engine = create_sqlite_engine(db)
    try:
        inspector = inspect(engine)
        assert not set(inspector.get_table_names()) & {m.__tablename__ for m in NEW_TABLES}
        assert {c["name"] for c in inspector.get_columns("runs")}.isdisjoint(
            {"mode", "current_node", "pending_gate", "control_request", "trace_id"}
        )
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 2
            assert (
                conn.execute(text(f"SELECT count(*) FROM agent_tasks WHERE id='{task_id}'")).scalar_one() == 1
            )
    finally:
        engine.dispose()
    upgrade(db)  # re-applying works and the run keeps its row
    assert _triggers(db, "spec_versions")


# ------------------------------------------------------------------------------- spec versions


def _draft(s: Session, ids: dict[str, str], clock: FixedClock) -> SpecVersionRow:
    return specs.create_draft(
        s, project_id=ids["a"], run_id=ids["run"], payload={"requirements": ["FR-001"]}, clock=clock
    )


def test_spec_versions_number_per_project_and_freeze_once(
    sessions: sessionmaker[Session], ids: dict[str, str], clock: FixedClock
) -> None:
    with sessions.begin() as s:
        first = _draft(s, ids, clock)
        second = _draft(s, ids, clock)
        other = specs.create_draft(s, project_id=ids["b"], run_id=ids["run_b"], payload={}, clock=clock)
        assert (first.version, second.version, other.version) == (1, 2, 1)
        assert specs.checksum_matches(first)
        frozen = specs.freeze(
            s,
            project_id=ids["a"],
            spec_version_id=first.id,
            approval_id=new_id("apr"),
            approved_by=ids["owner"],
            clock=clock,
        )
        assert frozen.status == "frozen" and frozen.frozen_at is not None
        with pytest.raises(specs.SpecVersionError, match="only a draft"):
            specs.freeze(
                s,
                project_id=ids["a"],
                spec_version_id=first.id,
                approval_id=new_id("apr"),
                approved_by=ids["owner"],
            )
        with pytest.raises(specs.SpecVersionError, match="not found"):
            specs.freeze(
                s,
                project_id=ids["b"],  # right ID, wrong project
                spec_version_id=second.id,
                approval_id=new_id("apr"),
                approved_by=ids["owner"],
            )
        assert specs.get_spec_version(s, ids["b"], first.id) is None


def test_checksum_is_canonical() -> None:
    assert specs.spec_checksum({"b": 1, "a": [1, 2]}) == specs.spec_checksum({"a": [1, 2], "b": 1})
    assert specs.spec_checksum({"a": 1}) != specs.spec_checksum({"a": 2})


@pytest.mark.security
@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE spec_versions SET checksum = '{z}' WHERE id = '{d}'", "immutable"),
        ("UPDATE spec_versions SET payload = '{{}}' WHERE id = '{d}'", "immutable"),
        ("UPDATE spec_versions SET status = 'frozen' WHERE id = '{d}'", "approval recorded"),
        ("UPDATE spec_versions SET status = 'draft' WHERE id = '{f}'", "frozen spec version is immutable"),
        (
            "UPDATE spec_versions SET approved_by = 'prn_x' WHERE id = '{f}'",
            "frozen spec version is immutable",
        ),
        ("DELETE FROM spec_versions WHERE id = '{d}'", "never deleted"),
        (
            "INSERT INTO spec_versions (id, project_id, run_id, version, status, checksum, payload,"
            " created_at, frozen_at, approval_id, approved_by)"
            " VALUES ('spv_x', '{p}', '{r}', 9, 'frozen', '{z}', '{{}}',"
            " '2026-11-23', '2026-11-23', 'apr_x', 'prn_x')",
            "created as a draft",
        ),
    ],
)
def test_database_triggers_protect_spec_versions(
    engine_sessions: tuple[Engine, sessionmaker[Session]],
    ids: dict[str, str],
    clock: FixedClock,
    statement: str,
    message: str,
) -> None:
    engine, sessions = engine_sessions
    with sessions.begin() as s:
        draft = _draft(s, ids, clock)
        frozen = _draft(s, ids, clock)
        specs.freeze(
            s,
            project_id=ids["a"],
            spec_version_id=frozen.id,
            approval_id=new_id("apr"),
            approved_by=ids["owner"],
            clock=clock,
        )
        draft_id, frozen_id = draft.id, frozen.id
    sql = statement.format(d=draft_id, f=frozen_id, z="0" * 64, p=ids["a"], r=ids["run"])
    with pytest.raises(DatabaseError, match=message), engine.begin() as conn:
        conn.execute(text(sql))


@pytest.mark.security
def test_checksum_tamper_is_detected_even_with_the_triggers_removed(
    engine_sessions: tuple[Engine, sessionmaker[Session]], ids: dict[str, str], clock: FixedClock
) -> None:
    """Negative control for the guard (D.3): an attacker with file access drops the triggers first."""
    engine, sessions = engine_sessions
    with sessions.begin() as s:
        draft_id = _draft(s, ids, clock).id
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER spec_versions_guard_update"))
        conn.execute(
            text(f"UPDATE spec_versions SET payload = '{{\"requirements\": []}}' WHERE id = '{draft_id}'")
        )
    with sessions() as s:
        row = specs.get_spec_version(s, ids["a"], draft_id)
        assert row is not None and not specs.checksum_matches(row)
        with pytest.raises(specs.SpecVersionError, match="does not match"):
            specs.freeze(
                s,
                project_id=ids["a"],
                spec_version_id=draft_id,
                approval_id=new_id("apr"),
                approved_by="prn_x",
            )


# ------------------------------------------------------------------------------------ run control


def test_control_requests_are_validated_and_project_scoped(
    sessions: sessionmaker[Session], ids: dict[str, str], clock: FixedClock
) -> None:
    with sessions.begin() as s:
        orch.set_control_request(s, ids["a"], ids["run"], "pause")
        assert orch.get_control_request(s, ids["a"], ids["run"]) == "pause"
        with pytest.raises(orch.OrchestrationStateError, match="unknown control request"):
            orch.set_control_request(s, ids["a"], ids["run"], "kill")  # type: ignore[arg-type]
        with pytest.raises(orch.OrchestrationStateError, match="not found"):
            orch.set_control_request(s, ids["b"], ids["run"], "cancel")
        orch.set_run_position(
            s,
            ids["a"],
            ids["run"],
            current_node="project_approval",
            pending_gate="project_approval",
            clock=clock,
        )
        run = s.get(Run, ids["run"])
        assert run is not None and run.pending_gate == "project_approval"


# ------------------------------------------------------------------------------------------- jobs


def test_jobs_are_claimed_once_in_order_and_respect_delays(
    sessions: sessionmaker[Session], ids: dict[str, str], clock: FixedClock
) -> None:
    with sessions.begin() as s:
        first = orch.enqueue_job(s, project_id=ids["a"], run_id=ids["run"], kind="start", clock=clock)
        clock.set(T0 + timedelta(seconds=1))
        later = orch.enqueue_job(
            s, project_id=ids["a"], run_id=ids["run"], kind="resume", delay_s=60, clock=clock
        )
        clock.set(T0 + timedelta(seconds=2))
        second = orch.enqueue_job(
            s,
            project_id=ids["b"],
            run_id=ids["run_b"],
            kind="gate_decision",
            payload={"approval_id": "apr_1"},
            clock=clock,
        )
        assert orch.pending_jobs(s) == 3
        claimed = orch.claim_next_job(s, clock=clock)
        assert claimed is not None and claimed.id == first.id and claimed.status == "running"
        claimed2 = orch.claim_next_job(s, clock=clock)
        assert claimed2 is not None and claimed2.id == second.id
        assert orch.claim_next_job(s, clock=clock) is None  # the delayed job is not due yet
        clock.set(T0 + timedelta(seconds=62))
        claimed3 = orch.claim_next_job(s, clock=clock)
        assert claimed3 is not None and claimed3.id == later.id and claimed3.attempts == 1
        orch.finish_job(s, first.id, "done", clock=clock)
        with pytest.raises(orch.OrchestrationStateError, match="only a running job"):
            orch.finish_job(s, first.id, "done")
        with pytest.raises(orch.OrchestrationStateError, match="can only finish"):
            orch.finish_job(s, second.id, "queued")
        with pytest.raises(orch.OrchestrationStateError, match="unknown job kind"):
            orch.enqueue_job(s, project_id=ids["a"], run_id=ids["run"], kind="shell")  # type: ignore[arg-type]
        with pytest.raises(orch.OrchestrationStateError, match="not found"):
            orch.enqueue_job(s, project_id=ids["b"], run_id=ids["run"], kind="start")


@pytest.mark.security
def test_database_rejects_unknown_job_kinds(
    engine_sessions: tuple[Engine, sessionmaker[Session]], ids: dict[str, str]
) -> None:
    engine = engine_sessions[0]
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO run_jobs (id, project_id, run_id, kind, payload, status, available_at, attempts,"
                f" created_at, updated_at) VALUES ('job_x', '{ids['a']}', '{ids['run']}', 'shell', '{{}}',"
                " 'queued', '2026-11-23', 0, '2026-11-23', '2026-11-23')"
            )
        )


# ---------------------------------------------------------------------------------- worker lease


def test_single_worker_lease(sessions: sessionmaker[Session], clock: FixedClock) -> None:
    with sessions.begin() as s:
        assert orch.acquire_lease(s, owner="wrk_a", pid=10, ttl_s=30, clock=clock).acquired
        assert not orch.acquire_lease(s, owner="wrk_b", pid=11, ttl_s=30, clock=clock).acquired
        assert orch.acquire_lease(s, owner="wrk_a", pid=10, ttl_s=30, clock=clock).acquired  # re-entrant
        clock.set(T0 + timedelta(seconds=20))
        assert orch.renew_lease(s, owner="wrk_a", clock=clock)
        clock.set(T0 + timedelta(seconds=45))  # 25 s since the last heartbeat: still alive
        assert not orch.acquire_lease(s, owner="wrk_b", pid=11, ttl_s=30, clock=clock).acquired
        clock.set(T0 + timedelta(seconds=51))  # 31 s: stale
        result = orch.acquire_lease(s, owner="wrk_b", pid=11, ttl_s=30, clock=clock)
        assert result.acquired and result.taken_over_from == "wrk_a"
        assert not orch.renew_lease(s, owner="wrk_a", clock=clock)  # the old worker learns it lost the lease
        assert not orch.release_lease(s, owner="wrk_a")
        assert orch.release_lease(s, owner="wrk_b")
        assert orch.acquire_lease(s, owner="wrk_c", pid=12, ttl_s=30, clock=clock).acquired


@pytest.mark.security
def test_lease_table_holds_a_single_row(
    engine_sessions: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine = engine_sessions[0]
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO worker_lease (id, owner, pid, acquired_at, heartbeat_at) "
                "VALUES (2, 'wrk_x', 1, '2026-11-23', '2026-11-23')"
            )
        )


# ----------------------------------------------------------------------------------------- events


def _gate_event(project: str, run: str) -> GateOpened:
    return GateOpened(
        event_id=new_id("evt"),
        ts=T0,
        project_id=project,
        run_id=run,
        actor="service:orchestrator",
        gate=GateName.PROJECT_APPROVAL,
    )


def test_events_have_gap_free_sequences_per_run(sessions: sessionmaker[Session], ids: dict[str, str]) -> None:
    with sessions.begin() as s:
        seqs = [orch.append_event(s, _gate_event(ids["a"], ids["run"])) for _ in range(3)]
        assert seqs == [1, 2, 3]
        assert orch.append_event(s, _gate_event(ids["b"], ids["run_b"])) == 1
        rows = orch.events_after(s, ids["a"], ids["run"], after_seq=1)
        assert [r.seq for r in rows] == [2, 3]
        assert rows[0].kind == "gate.opened" and rows[0].payload["gate"] == "project_approval"
        with pytest.raises(orch.OrchestrationStateError, match="not found"):
            orch.events_after(s, ids["b"], ids["run"])
        with pytest.raises(orch.OrchestrationStateError, match="not found"):
            orch.append_event(s, _gate_event(ids["b"], ids["run"]))  # event claims another project's run


@pytest.mark.security
def test_run_events_cannot_be_edited(
    engine_sessions: tuple[Engine, sessionmaker[Session]], ids: dict[str, str]
) -> None:
    engine, sessions = engine_sessions
    with sessions.begin() as s:
        orch.append_event(s, _gate_event(ids["a"], ids["run"]))
    with pytest.raises(DatabaseError, match="never edited"), engine.begin() as conn:
        conn.execute(text("UPDATE run_events SET kind = 'x'"))


# ----------------------------------------------------------------------------------- node effects


def test_node_effects_move_forward_and_resume_by_key(
    sessions: sessionmaker[Session], ids: dict[str, str], clock: FixedClock
) -> None:
    key = orch.effect_key(ids["run"], "freeze_spec", "spec", 1)
    assert key == f"{ids['run']}:freeze_spec:spec:1"
    with sessions.begin() as s:
        effect = orch.begin_effect(
            s, project_id=ids["a"], run_id=ids["run"], node="freeze_spec", key=key, clock=clock
        )
        again = orch.begin_effect(s, project_id=ids["a"], run_id=ids["run"], node="freeze_spec", key=key)
        assert again.id == effect.id  # resume returns the same record
        assert [e.key for e in orch.unconfirmed_effects(s, ids["run"])] == [key]
        orch.advance_effect(s, key, "performed", result_ref="spv_x", clock=clock)
        with pytest.raises(orch.OrchestrationStateError, match="only moves forward"):
            orch.advance_effect(s, key, "intent")
        orch.advance_effect(s, key, "confirmed", clock=clock)
        assert orch.unconfirmed_effects(s, ids["run"]) == []
        with pytest.raises(orch.OrchestrationStateError, match="only moves forward"):
            orch.advance_effect(s, key, "confirmed")
        with pytest.raises(orch.OrchestrationStateError, match="another run or node"):
            orch.begin_effect(s, project_id=ids["a"], run_id=ids["run"], node="final_report", key=key)


@pytest.mark.security
def test_database_keeps_node_effects_forward_only(
    engine_sessions: tuple[Engine, sessionmaker[Session]], ids: dict[str, str], clock: FixedClock
) -> None:
    engine, sessions = engine_sessions
    key = orch.effect_key(ids["run"], "final_report", "report")
    with sessions.begin() as s:
        orch.begin_effect(
            s, project_id=ids["a"], run_id=ids["run"], node="final_report", key=key, clock=clock
        )
        orch.advance_effect(s, key, "confirmed", clock=clock)
    with pytest.raises(DatabaseError, match="only moves forward"), engine.begin() as conn:
        conn.execute(text("UPDATE node_effects SET phase = 'intent'"))


# ----------------------------------------------------------------------------------------- plans


def test_plans_are_stored_once_and_latest_is_returned(
    engine_sessions: tuple[Engine, sessionmaker[Session]], ids: dict[str, str], clock: FixedClock
) -> None:
    engine, sessions = engine_sessions
    with sessions.begin() as s:
        spec_id = _draft(s, ids, clock).id
        orch.add_plan(
            s,
            plan_id=new_id("pln"),
            project_id=ids["a"],
            run_id=ids["run"],
            spec_version_id=spec_id,
            source="template",
            payload={"tasks": []},
            report={"ok": True},
            clock=clock,
        )
        clock.set(T0 + timedelta(minutes=1))
        newer = orch.add_plan(
            s,
            plan_id=new_id("pln"),
            project_id=ids["a"],
            run_id=ids["run"],
            spec_version_id=spec_id,
            source="template",
            payload={"tasks": [1]},
            report={"ok": True},
            clock=clock,
        )
        latest = orch.latest_plan(s, ids["a"], ids["run"])
        assert latest is not None and latest.id == newer.id
        assert orch.latest_plan(s, ids["b"], ids["run"]) is None
    with pytest.raises(DatabaseError, match="never edited"), engine.begin() as conn:
        conn.execute(text("UPDATE task_plans SET source = 'model'"))


def test_attempt_keys_agree_between_contract_and_repository() -> None:
    assert attempt_key("run_1", "architect", "tsk_1", 2) == runs.attempt_key("run_1", "architect", "tsk_1", 2)
