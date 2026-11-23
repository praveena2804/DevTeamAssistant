"""Advancing one run through the graph (D.3/D.4): checkpointer, control flags, outcomes.

``advance`` is what the worker calls for one job. It:

1. refuses to touch a finished run; applies a pending ``cancel`` first;
2. moves the run to ``running`` through the run state machine;
3. streams the graph (thread ID = run ID, checkpoints in ``data/checkpoints.db``) and, **after every
   step**, reads the run's control flag: ``pause`` → *paused*, ``cancel`` → *cancelled*. The last
   checkpoint is kept, so a paused run resumes exactly where it stopped (cooperative, roadmap §6.4);
4. maps the result honestly: an ``interrupt()`` → *waiting for human* (the pending gate is recorded and a
   ``gate.opened`` event emitted); ``ResourceWait`` → *waiting for resources* with a delayed resume job;
   a ``StepError`` → *blocked*/*failed* with its reason; anything unexpected → *failed* (type name only).

The checkpointer never unpickles: the serializer has pickle fallback off and no extra msgpack types, and
the state holds only JSON-like references.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from devteam_core import tracing
from devteam_core.contracts.events import GateOpened
from devteam_core.contracts.orchestration import GATE_FOR_NODE, GateResume, NodeName
from devteam_core.contracts.statuses import RunStatus
from devteam_core.ids import new_id
from devteam_orchestration.graph import emit, finish_run
from devteam_orchestration.runtime import Runtime
from devteam_orchestration.steps import ORCHESTRATOR, ResourceWait, StepError
from devteam_state.machines import transition_run
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import runs

R = RunStatus
TERMINAL = frozenset({R.COMPLETED, R.CANCELLED, R.FAILED, R.BLOCKED})
Result = Literal[
    "completed",
    "cancelled",
    "blocked",
    "failed",
    "paused",
    "waiting_for_human",
    "waiting_for_resources",
    "stopped",
]


def open_checkpointer(path: Path) -> Any:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer  # noqa: PLC0415
    from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)
    saver = SqliteSaver(conn, serde=serde)
    saver.setup()
    return saver


@dataclass(frozen=True)
class Advance:
    result: Result
    reason: str | None = None
    pending_gate: str | None = None


def _status(rt: Runtime, project_id: str, run_id: str) -> tuple[RunStatus, str, str | None]:
    with rt.sessions() as session:
        run = runs.get_run(session, project_id, run_id)
        if run is None:
            raise StepError("run.unknown", "the run does not exist")
        return R(run.status), run.control_request, run.trace_id


def _move(
    rt: Runtime, project_id: str, run_id: str, to: RunStatus, reason: str, *, clear_control: bool = False
) -> None:
    with rt.sessions.begin() as session:
        run = runs.get_run(session, project_id, run_id)
        assert run is not None  # noqa: S101 - checked by the caller
        if R(run.status) is not to:
            transition_run(
                session,
                run,
                to,
                actor=ORCHESTRATOR,
                reason=reason[:200],
                denial_sessions=rt.sessions,
                clock=rt.clock,
            )
        if clear_control:
            run.control_request = "none"


def _trace_context(rt: Runtime, project_id: str, run_id: str, trace_id: str | None) -> tracing.TraceContext:
    """All jobs of a run share one trace ID (stored on the run), so the trace viewer shows one tree."""
    if trace_id is None:
        trace_id = tracing.new_root().trace_id
        with rt.sessions.begin() as session:
            run = runs.get_run(session, project_id, run_id)
            if run is not None:
                run.trace_id = trace_id
    return tracing.child_of(tracing.TraceContext(trace_id=trace_id, span_id="0" * 15 + "1"))


def advance(  # noqa: PLR0911, PLR0912 - one exit per run outcome
    rt: Runtime,
    graph: Any,
    *,
    project_id: str,
    run_id: str,
    kind: Literal["start", "resume", "gate_decision"],
    payload: dict[str, Any] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> Advance:
    status, control, trace_id = _status(rt, project_id, run_id)
    if status in TERMINAL:
        return Advance(status.value)  # type: ignore[arg-type]
    if control == "cancel":
        finish_run(rt, project_id, run_id, R.CANCELLED, "cancelled on request")
        _clear_control(rt, project_id, run_id)
        return Advance("cancelled", "cancelled on request")

    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": rt.settings.orchestration.recursion_limit,
    }
    snapshot = graph.get_state(config)
    graph_input: Any
    if kind == "gate_decision":
        from langgraph.types import Command  # noqa: PLC0415

        resume = GateResume.model_validate(payload or {})
        graph_input = Command(resume=resume.model_dump(mode="json"))
    elif not snapshot.values:
        graph_input = {"project_id": project_id, "run_id": run_id}
    else:
        graph_input = None  # continue from the last checkpoint
    if kind != "gate_decision" and snapshot.values and snapshot.interrupts:
        return _waiting(rt, project_id, run_id, snapshot)  # still waiting: a plain resume cannot pass a gate

    _move(rt, project_id, run_id, R.RUNNING, f"{kind} job")
    with (
        tracing.use_context(_trace_context(rt, project_id, run_id, trace_id)),
        rt.tracer.span("graph.run", project_id=project_id, run_id=run_id) as span,
    ):
        try:
            for _update in graph.stream(graph_input, config, stream_mode="updates"):
                if should_stop():  # worker shutdown or lost lease: stop at this node boundary
                    span.set("outcome", "stopped")
                    return Advance("stopped", "the worker stopped at a node boundary")
                _, control, _ = _status(rt, project_id, run_id)
                if control == "pause":
                    _move(rt, project_id, run_id, R.PAUSED, "paused on request", clear_control=True)
                    span.set("outcome", "paused")
                    return Advance("paused")
                if control == "cancel":
                    finish_run(rt, project_id, run_id, R.CANCELLED, "cancelled on request")
                    _clear_control(rt, project_id, run_id)
                    span.set("outcome", "cancelled")
                    return Advance("cancelled", "cancelled on request")
        except ResourceWait as wait:
            _move(rt, project_id, run_id, R.WAITING_FOR_RESOURCES, wait.code)
            with rt.sessions.begin() as session:
                orch.enqueue_job(
                    session,
                    project_id=project_id,
                    run_id=run_id,
                    kind="resume",
                    delay_s=rt.settings.orchestration.resource_wait_s,
                    clock=rt.clock,
                )
            span.set("outcome", "waiting")
            return Advance("waiting_for_resources", wait.message)
        except StepError as error:
            target = R.FAILED if error.status == "failed" else R.BLOCKED
            finish_run(rt, project_id, run_id, target, error.message)
            span.set("outcome", target.value)
            span.set("error.code", error.code)
            return Advance(target.value, error.message)  # type: ignore[arg-type]
        except Exception as exc:  # an unexpected fault: fail the run, never pretend it succeeded
            finish_run(rt, project_id, run_id, R.FAILED, f"internal error ({type(exc).__name__})")
            span.set("outcome", "failed")
            return Advance("failed", f"internal error ({type(exc).__name__})")

    snapshot = graph.get_state(config)
    if snapshot.next:
        return _waiting(rt, project_id, run_id, snapshot)
    final, _, _ = _status(rt, project_id, run_id)
    return Advance(final.value if final in TERMINAL else "failed", None)  # type: ignore[arg-type]


def _clear_control(rt: Runtime, project_id: str, run_id: str) -> None:
    with rt.sessions.begin() as session:
        orch.set_control_request(session, project_id, run_id, "none")


def _waiting(rt: Runtime, project_id: str, run_id: str, snapshot: Any) -> Advance:
    node = NodeName(snapshot.next[0])
    gate = GATE_FOR_NODE.get(node)
    if gate is None:
        finish_run(rt, project_id, run_id, R.FAILED, f"stopped at a non-gate node ({node.value})")
        return Advance("failed", "the run stopped at a node that is not a gate")
    status, _, _ = _status(rt, project_id, run_id)
    if status is not R.WAITING_FOR_HUMAN:
        _move(rt, project_id, run_id, R.WAITING_FOR_HUMAN, f"waiting at {gate.value}")
        with rt.sessions.begin() as session:
            orch.set_run_position(
                session, project_id, run_id, current_node=node.value, pending_gate=gate.value, clock=rt.clock
            )
        emit(
            rt,
            GateOpened(
                event_id=new_id("evt", rt.clock),
                ts=rt.clock.now(),
                project_id=project_id,
                run_id=run_id,
                actor=ORCHESTRATOR,
                gate=gate,
            ),
        )
    return Advance("waiting_for_human", pending_gate=gate.value)
