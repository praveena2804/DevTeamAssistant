"""Running one agent task from the graph (D.3): idempotent, crash-safe, real executor only.

* **One task per step.** A node finds or creates its task through the two-phase effects ledger
  (``node_effects``, key ``run_id:node:scope:1``), so a node re-run after a crash reuses the same task
  and never creates a second one.
* **Completed work is not repeated.** A completed task returns its stored output (``agent_outputs``);
  the agent is not called again (roadmap §6.7).
* **Crashes are not failures (D-40).** A task found ``running`` was interrupted: its attempt is marked
  ``interrupted``, the attempt it used is given back (``max_attempts`` + 1, within the contract cap of
  4), and the task becomes ``ready``. More than ``max_crash_resumes`` crashes in a row block the task.
* **Honest outcomes.** The executor's status is reported as it is: completed, failed, blocked or waiting
  for resources. Nothing is retried here except a whole-task re-attempt the Head failure router allows
  (F-10d); policy and budget refusals are never retried (F-10c).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select

from devteam_agents.head.failure_router import Failure, FailureSource, RouteAction, route_crash, route_failure
from devteam_core.contracts.agents import AGENT_IO, AgentIO
from devteam_core.contracts.errors import ErrorCategory, ErrorRef
from devteam_core.contracts.statuses import AgentRole, Stage, TaskStatus
from devteam_core.contracts.tasks import MAX_TASK_ATTEMPTS, AgentTask
from devteam_core.ids import new_id
from devteam_harness.executor import Disposition, ExecutionResult, TaskRun
from devteam_orchestration.runtime import Runtime
from devteam_state.machines import transition_task
from devteam_state.models import TaskAttempt
from devteam_state.repositories import audit, runs
from devteam_state.repositories import orchestration as orch

ORCHESTRATOR = "service:orchestrator"
T = TaskStatus


class StepError(RuntimeError):
    """A step cannot continue; ``status`` is the run status it leads to (blocked or failed)."""

    def __init__(self, code: str, message: str, *, status: Literal["blocked", "failed"] = "blocked") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ResourceWait(RuntimeError):  # noqa: N818 - a signal, not an error
    """The step needs memory or the local model; the run waits and resumes later (not a failure)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StepOutcome:
    task_id: str
    status: TaskStatus
    output: dict[str, Any] | None
    error: ErrorRef | None = None
    replayed: bool = False


# ------------------------------------------------------------------------------------- task records


def ensure_task(rt: Runtime, *, node: str, scope: str, build: Any) -> str:
    """The task for ``(run, node, scope)``: created once (``build(task_id) -> AgentTask``), then reused."""
    task_id = new_id("tsk", rt.clock)
    task: AgentTask = build(task_id)
    key = orch.effect_key(task.run_id, node, scope)
    with rt.sessions.begin() as session:
        effect = orch.begin_effect(
            session, project_id=task.project_id, run_id=task.run_id, node=node, key=key, clock=rt.clock
        )
        if effect.result_ref is not None:
            return effect.result_ref
        runs.add_task(session, task, clock=rt.clock)
        orch.advance_effect(session, key, "confirmed", result_ref=task.task_id, clock=rt.clock)
    return task.task_id


def task_row_status(rt: Runtime, project_id: str, task_id: str) -> TaskStatus:
    with rt.sessions() as session:
        row = runs.get_task(session, project_id, task_id)
        if row is None:
            raise StepError("task.unknown", "the step's task no longer exists")
        return TaskStatus(row.status)


def load_task(rt: Runtime, project_id: str, task_id: str) -> AgentTask:
    with rt.sessions() as session:
        row = runs.get_task(session, project_id, task_id)
        if row is None:
            raise StepError("task.unknown", "the step's task no longer exists")
        return runs.load_task_contract(row)


def set_task_status(rt: Runtime, project_id: str, task_id: str, to: TaskStatus, reason: str) -> None:
    with rt.sessions.begin() as session:
        row = runs.get_task(session, project_id, task_id)
        if row is None or TaskStatus(row.status) is to:
            return
        if to is T.BLOCKED:
            row.payload = {**row.payload, "blocker_reason": reason[:2_000]}
        transition_task(
            session, row, to, actor=ORCHESTRATOR, reason=reason[:200], denial_sessions=rt.sessions
        )


def stored_output(rt: Runtime, project_id: str, task_id: str) -> dict[str, Any] | None:
    with rt.sessions() as session:
        row = orch.get_agent_output(session, project_id, task_id)
        return dict(row.payload) if row is not None else None


# ---------------------------------------------------------------------------------- crash recovery


def recover_crash(rt: Runtime, project_id: str, task_id: str) -> None:
    """D-40: a task left ``running`` by a crash. Never counted as a failed attempt."""
    max_resumes = rt.settings.orchestration.max_crash_resumes
    with rt.sessions.begin() as session:
        row = runs.get_task(session, project_id, task_id)
        if row is None or TaskStatus(row.status) is not T.RUNNING:
            return
        attempts = list(
            session.scalars(
                select(TaskAttempt).where(TaskAttempt.task_id == task_id).order_by(TaskAttempt.attempt.desc())
            )
        )
        for attempt in attempts:
            if attempt.status == "started":
                attempt.status = "interrupted"
                attempt.error_code = "worker.crashed"
                attempt.finished_at = rt.clock.now()
        crashes = 0
        for attempt in attempts:
            if attempt.status != "interrupted":
                break
            crashes += 1
        decision = route_crash(crashes, max_crash_resumes=max_resumes)
        if row.max_attempts < MAX_TASK_ATTEMPTS:  # give the crashed attempt back (not a failure)
            row.max_attempts += 1
            row.payload = {**row.payload, "max_attempts": row.max_attempts}
        transition_task(
            session,
            row,
            T.READY,
            actor=ORCHESTRATOR,
            reason="interrupted by a crash",
            denial_sessions=rt.sessions,
        )
        if decision.action is RouteAction.BLOCK:
            row.payload = {**row.payload, "blocker_reason": decision.reason}
            transition_task(
                session,
                row,
                T.BLOCKED,
                actor=ORCHESTRATOR,
                reason="repeated crashes",
                denial_sessions=rt.sessions,
            )
        audit.append(
            session,
            action="task.crash_recovered",
            outcome="denied" if decision.action is RouteAction.BLOCK else "success",
            principal_id=ORCHESTRATOR,
            project_id=project_id,
            target_type="task",
            target_id=task_id,
            details={"run_id": row.run_id, "consecutive_crashes": crashes, "decision": decision.action.value},
            clock=rt.clock,
        )


# ------------------------------------------------------------------------------------------ execute


def run_task(  # noqa: PLR0912 - one exit per task outcome
    rt: Runtime,
    *,
    project_id: str,
    task_id: str,
    stage: Stage,
    given: AgentIO,
    spec_approved: bool,
) -> StepOutcome:
    """Execute (or replay) one agent task through the real executor and record its output."""
    status = task_row_status(rt, project_id, task_id)
    if status is T.RUNNING:
        recover_crash(rt, project_id, task_id)
        status = task_row_status(rt, project_id, task_id)
    if status in {T.COMPLETED, T.PARTIALLY_COMPLETED}:
        return StepOutcome(task_id, status, stored_output(rt, project_id, task_id), replayed=True)
    if status in {T.BLOCKED, T.SKIPPED, T.CANCELLED}:
        return StepOutcome(task_id, status, None)
    if status is T.PENDING:
        set_task_status(rt, project_id, task_id, T.READY, "dispatched")
    if status is T.FAILED:
        return StepOutcome(task_id, status, None)

    files = rt.files_for(project_id)
    reattempts = rt.budgets.retries.task_reattempts
    while True:
        result: ExecutionResult = rt.executor.execute(
            TaskRun(
                project_id=project_id,
                task_id=task_id,
                stage=stage,
                given=given,
                files=files,
                spec_approved=spec_approved,
            )
        )
        if result.disposition in {Disposition.COMPLETED, Disposition.REPLAYED}:
            output = result.output.model_dump(mode="json") if result.output is not None else None
            if output is not None:
                with rt.sessions.begin() as session:
                    orch.save_agent_output(
                        session,
                        project_id=project_id,
                        run_id=given.run_id,
                        task_id=task_id,
                        agent_type=AGENT_IO_ROLE[type(given)].value,
                        payload=output,
                        clock=rt.clock,
                    )
            else:
                output = stored_output(rt, project_id, task_id)
            return StepOutcome(
                task_id, T.COMPLETED, output, replayed=result.disposition is Disposition.REPLAYED
            )
        if result.disposition is Disposition.WAITING_FOR_RESOURCES:
            error = result.error
            raise ResourceWait(
                error.code if error else "resource.unavailable", error.message if error else "waiting"
            )
        if result.disposition is Disposition.RETRY and result.status is T.READY:
            continue  # the executor recovered an interrupted attempt itself; run it now
        if result.status is T.FAILED and result.error is not None:
            task = load_task(rt, project_id, task_id)
            route = route_failure(
                Failure(
                    task=task,
                    source=FailureSource.AGENT_TASK,
                    category=result.error.category,
                    code=result.error.code,
                ),
                reattempt_allowance=reattempts,
                write_scopes=rt.write_scopes,
            )
            if route.action is RouteAction.RETRY_TASK:
                set_task_status(rt, project_id, task_id, T.READY, route.reason)
                continue
            if route.action is RouteAction.WAIT_FOR_RESOURCES:
                raise ResourceWait(result.error.code, result.error.message)
        return StepOutcome(task_id, result.status, None, error=result.error)


AGENT_IO_ROLE: dict[type[AgentIO], AgentRole] = {io[0]: role for role, io in AGENT_IO.items()}


def require_completed(outcome: StepOutcome, what: str) -> dict[str, Any]:
    """The output of a completed step, or a ``StepError`` describing honestly why there is none."""
    if outcome.status is T.COMPLETED and outcome.output is not None:
        return outcome.output
    error = outcome.error
    if error is not None and error.category in {ErrorCategory.POLICY_DENIED, ErrorCategory.BUDGET_EXHAUSTED}:
        raise StepError(error.code, f"{what} was refused: {error.message}")
    detail = f"{error.code}: {error.message}" if error is not None else outcome.status.value
    raise StepError("agent.not_completed", f"{what} did not complete ({detail})", status="blocked")
