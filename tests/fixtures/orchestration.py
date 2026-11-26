"""Test environment for Part D orchestration: a real migrated state DB, real config, real runtime.

The runtime always uses the real Ollama provider. Tests that need a model answer are marked ``llm``
(local Ollama, never in CI). Fast tests point the provider at a closed loopback port so any model call
fails honestly ("Ollama unreachable") instead of being simulated.
"""

from __future__ import annotations

import io
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.config import Settings, load_settings
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import ApprovalType, Stage
from devteam_harness.iam import LOCAL_HUMAN_ROLES, Principal
from devteam_orchestration.runtime import Runtime, build_runtime
from devteam_services.intake import IncomingFile, IntakeService
from devteam_services.workspace import WorkspaceManager
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import principals, projects, runs
from tests.fixtures.brds import brd_markdown


def closed_port() -> int:
    """A loopback port with nothing listening (bound and released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_settings(tmp: Path, *, ollama_url: str | None = None) -> Settings:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("DEVTEAM_")},
        "DEVTEAM_PATHS__DATA_DIR": str(tmp / "data"),
        "DEVTEAM_PATHS__WORKSPACES_DIR": str(tmp / "workspaces"),
        "DEVTEAM_LOGGING__FILE_ENABLED": "false",
    }
    if ollama_url is not None:
        env["DEVTEAM_OLLAMA__BASE_URL"] = ollama_url
    return load_settings(environ=env, dotenv_path=tmp / "no.env")


@dataclass
class Env:
    settings: Settings
    sessions: sessionmaker[Session]
    rt: Runtime
    graph: Any  # None unless the graph was built (with_graph=True)
    checkpointer: Any
    owner: Principal
    project_id: str
    run_id: str

    def approve(
        self,
        approval_type: ApprovalType,
        *,
        decision: ApprovalDecision = ApprovalDecision.APPROVED,
        spec_version_id: str | None = None,
        spec_checksum: str | None = None,
        subject_ref: str | None = None,
    ) -> str:
        return self.rt.approvals.record(
            self.owner,
            project_id=self.project_id,
            run_id=self.run_id,
            approval_type=approval_type,
            decision=decision,
            spec_version_id=spec_version_id,
            spec_checksum=spec_checksum,
            subject_ref=subject_ref,
        ).approval_id

    def store_analysis(self, task_id: str, analysis: dict[str, Any] | None = None) -> None:
        """Part G: a stored BRD analysis for a test that starts after the analyst step (default: no needs)."""
        from devteam_core.contracts.agents import BrdAnalystOutput  # noqa: PLC0415
        from devteam_core.contracts.records import GapCategory  # noqa: PLC0415

        payload = analysis or {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "checklist": [{"category": c.value, "status": "missing_or_unclear"} for c in GapCategory],
        }
        from devteam_core.contracts.statuses import AgentRole  # noqa: PLC0415
        from devteam_core.contracts.tasks import AgentTask  # noqa: PLC0415

        valid = BrdAnalystOutput.model_validate(payload).model_dump(mode="json")
        with self.sessions.begin() as session:
            if runs.get_task(session, self.project_id, task_id) is None:
                runs.add_task(
                    session,
                    AgentTask(
                        task_id=task_id, project_id=self.project_id, run_id=self.run_id,
                        agent_type=AgentRole.BRD_ANALYST, operation_id="brd_analyst.analyse_section",
                        objective="Analyse the BRD", done_criteria=("checklist returned",),
                    ),
                )  # fmt: skip
            orch.save_agent_output(
                session, project_id=self.project_id, run_id=self.run_id, task_id=task_id,
                agent_type="brd_analyst", payload=valid,
            )  # fmt: skip

    def close(self) -> None:
        if self.checkpointer is not None:
            self.checkpointer.conn.close()
        self.rt.close()


def make_env(
    tmp: Path, *, ollama_url: str | None = None, with_brd: bool = True, with_graph: bool = False
) -> Env:
    settings = test_settings(tmp, ollama_url=ollama_url)
    upgrade(settings.state_db_path)
    engine = create_sqlite_engine(settings.state_db_path)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        record = principals.ensure_local_human(session, display_name="Owner", roles=LOCAL_HUMAN_ROLES)
        owner = Principal(
            id=record.id, kind=record.kind, display_name=record.display_name, roles=record.roles
        )
        project = projects.create(session, name="Library", description="", created_by=owner.id)
        project_id = project.id
    workspaces = WorkspaceManager(settings.workspaces_dir)
    workspaces.create(project_id)
    if with_brd:
        intake = IntakeService(settings.uploads, workspaces)
        with sessions.begin() as session:
            outcomes = intake.process(
                session,
                project_id=project_id,
                principal_id=owner.id,
                files=[IncomingFile(filename="library.md", stream=io.BytesIO(brd_markdown()))],
            )
        assert outcomes[0].status == "extracted", outcomes
    with sessions.begin() as session:
        run_id = runs.create_run(session, project_id=project_id, run_kind="initial", stage=Stage.INTAKE).id
    rt = build_runtime(settings, sessions)
    checkpointer: Any = None
    graph: Any = None
    if with_graph:
        from devteam_orchestration.execution import open_checkpointer  # noqa: PLC0415
        from devteam_orchestration.graph import build_graph  # noqa: PLC0415

        checkpointer = open_checkpointer(settings.checkpoints_db_path)
        graph = build_graph(rt, checkpointer)
    return Env(settings, sessions, rt, graph, checkpointer, owner, project_id, run_id)


def assume_free_ram(env: Env, megabytes: int = 64_000) -> None:
    """Make the run's memory checks see ``megabytes`` free instead of the host's real RAM (KI-047).

    Both probes are pinned: the gateway's per-model check and the Budget Manager's check, which the Agent
    Executor consults before every task. Without this, a test that mocks or removes Ollama still depends on
    how much memory the machine happens to have free.
    """
    assert env.rt.gateway is not None
    env.rt.gateway._free_ram = lambda: megabytes
    env.rt.executor._s.budgets._memory_probe = lambda: megabytes


def frozen_spec(env: Env, in_scope: tuple[str, ...] = ("FR-001",)) -> tuple[str, str, str]:
    """A genuinely approved and frozen spec version for ``env``'s run: ``(spec_id, checksum, approval_id)``.

    It goes through the real Approval Service and Spec Freeze service (no shortcut), and records the
    frozen version on the run as the ``freeze_spec`` node does.
    """
    from devteam_orchestration import services  # noqa: PLC0415
    from devteam_state.repositories import spec_versions as specs  # noqa: PLC0415

    with env.sessions.begin() as session:
        draft = specs.create_draft(
            session,
            project_id=env.project_id,
            run_id=env.run_id,
            payload={
                "in_scope": list(in_scope),
                "excluded": [],
                "analysis_task_id": None,
                "finalizer_task_id": None,
            },
        )
        spec_id, checksum = draft.id, draft.checksum
    approval = env.approve(ApprovalType.SPEC_APPROVAL, spec_version_id=spec_id, spec_checksum=checksum)
    services.freeze_spec(
        env.sessions,
        env.rt.approvals,
        project_id=env.project_id,
        run_id=env.run_id,
        spec_version_id=spec_id,
        approval_id=approval,
    )
    with env.sessions.begin() as session:
        run = runs.get_run(session, env.project_id, env.run_id)
        assert run is not None
        run.spec_version_id, run.spec_checksum = spec_id, checksum
    return spec_id, checksum, approval
