"""D.2 ``agent_outputs`` (migration 0006): stored once per task, never edited, project-scoped."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.statuses import AgentRole, Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_state import create_sqlite_engine, downgrade, session_factory, upgrade
from devteam_state.models import AgentOutputRow, Principal, Project
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import runs

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


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
        out["run"] = runs.create_run(s, project_id=out["a"], run_kind="initial", stage=Stage.ANALYSIS).id
        task = AgentTask(
            task_id=new_id("tsk"),
            project_id=out["a"],
            run_id=out["run"],
            agent_type=AgentRole.BRD_ANALYST,
            operation_id="brd_analyst.analyse_section",
            objective="Analyse",
            done_criteria=("done",),
        )
        runs.add_task(s, task)
        out["task"] = task.task_id
    return out


def test_an_output_is_stored_once_and_a_repeat_returns_it(
    sessions: sessionmaker[Session], ids: dict[str, str]
) -> None:
    with sessions.begin() as s:
        first = orch.save_agent_output(
            s,
            project_id=ids["a"],
            run_id=ids["run"],
            task_id=ids["task"],
            agent_type="brd_analyst",
            payload={"n": 1},
        )
        again = orch.save_agent_output(
            s,
            project_id=ids["a"],
            run_id=ids["run"],
            task_id=ids["task"],
            agent_type="brd_analyst",
            payload={"n": 2},
        )
        assert again is first and again.payload == {"n": 1}
    with sessions() as s:
        stored = orch.get_agent_output(s, ids["a"], ids["task"])
        assert stored is not None and stored.payload == {"n": 1}
        assert orch.get_agent_output(s, ids["b"], ids["task"]) is None  # project-scoped


def test_another_projects_run_is_refused(sessions: sessionmaker[Session], ids: dict[str, str]) -> None:
    with sessions.begin() as s, pytest.raises(orch.OrchestrationStateError):
        orch.save_agent_output(
            s, project_id=ids["b"], run_id=ids["run"], task_id=ids["task"], agent_type="x", payload={}
        )


def test_a_stored_output_is_never_edited(sessions: sessionmaker[Session], ids: dict[str, str]) -> None:
    with sessions.begin() as s:
        orch.save_agent_output(
            s,
            project_id=ids["a"],
            run_id=ids["run"],
            task_id=ids["task"],
            agent_type="brd_analyst",
            payload={"n": 1},
        )
    with pytest.raises(DatabaseError, match="never edited"), sessions.begin() as s:
        s.execute(text("UPDATE agent_outputs SET payload = '{}'"))


def test_downgrade_removes_only_the_new_table(
    db: Path, sessions: sessionmaker[Session], ids: dict[str, str]
) -> None:
    downgrade(db, "0005_orchestration")
    engine = create_sqlite_engine(db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "agent_outputs" not in tables
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM agent_tasks")).scalar() == 1
    finally:
        engine.dispose()
    upgrade(db)
    engine = create_sqlite_engine(db)
    try:
        assert "agent_outputs" in set(inspect(engine).get_table_names())
        assert AgentOutputRow.__tablename__ == "agent_outputs"
    finally:
        engine.dispose()
