"""Budget Manager (HC-05, HS-13): every F-9 limit is checked **before** the next action.

A :class:`TaskBudget` is opened by the executor for one task. It loads the persisted usage of the task
and of its run (so a restart keeps counting), checks each limit before a model or tool call, records
what was actually used afterwards, and persists the counters in ``usage_counters``.

Reaching a limit raises :class:`BudgetExhaustedError` (category ``budget_exhausted``; never retried,
F-10c) and writes a ``budget.exhausted`` audit event naming the dimension. Too little free memory
(F-9n) raises :class:`ResourceUnavailableError` instead, which pauses the run rather than failing it.

Dimensions and their limits:

========================  =====  ==================================================================
``output_tokens``         F-9a   requested answer length per model call
``input_context``         F-9b   estimated input tokens per model call
``task_model_calls``      F-9e   model calls per task
``task_tokens``           F-9f   input + output tokens per task (reserved before, actual after)
``task_time``             F-9g   wall-clock minutes per task
``task_tool_calls``       F-9h   tool calls per task
``run_active_time``       F-9k   active hours per run (time spent executing tasks only)
``run_tokens``            F-9l   tokens per run
``run_model_calls``       F-9m   model calls per run
========================  =====  ==================================================================
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

import psutil
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.llm import ModelUsage
from devteam_core.contracts.tasks import AgentTask
from devteam_harness.budgets.config import BudgetProfile, Budgets
from devteam_harness.errors import BudgetExhaustedError, ResourceUnavailableError
from devteam_harness.guardrails.policy import AuditSink
from devteam_state.repositories.execution import Usage, add_usage, get_usage

CHARS_PER_TOKEN_ESTIMATE = 3  # conservative (real tokenisers average ~4); the F context manager refines it


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def free_ram_mb() -> int:
    return int(psutil.virtual_memory().available // (1024 * 1024))


class BudgetManager:
    def __init__(
        self,
        budgets: Budgets,
        sessions: sessionmaker[Session],
        sink: AuditSink,
        *,
        memory_probe: Callable[[], int] = free_ram_mb,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budgets = budgets
        self._sessions = sessions
        self._sink = sink
        self._memory_probe = memory_probe
        self._monotonic = monotonic

    def free_memory_mb(self) -> int:
        return self._memory_probe()

    def open_task(self, task: AgentTask) -> TaskBudget:
        profile = self.budgets.profile(task.budget_profile)
        with self._sessions() as session:
            task_usage = get_usage(session, task.project_id, task.run_id, task.task_id)
            run_usage = get_usage(session, task.project_id, task.run_id)
        return TaskBudget(self, task, profile, task_usage, run_usage)

    # used by TaskBudget ----------------------------------------------------------------------------

    def _persist(self, task: AgentTask, delta: Usage) -> None:
        with self._sessions.begin() as session:
            add_usage(
                session, project_id=task.project_id, run_id=task.run_id, task_id=task.task_id, delta=delta
            )
            add_usage(session, project_id=task.project_id, run_id=task.run_id, task_id=None, delta=delta)

    def _exhausted(self, task: AgentTask, dimension: str, limit: int, used: int) -> BudgetExhaustedError:
        self._sink.record(
            action="budget.exhausted",
            outcome="denied",
            principal_id="service:executor",
            project_id=task.project_id,
            details={
                "dimension": dimension,
                "limit": limit,
                "used": used,
                "task_id": task.task_id,
                "run_id": task.run_id,
                "budget_profile": task.budget_profile,
                "budgets_digest": self.budgets.digest,
            },
        )
        return BudgetExhaustedError(dimension, limit, used)


class TaskBudget:
    """Live budget for one task. Thread-safe; the gateway and tool proxies share one instance."""

    def __init__(
        self,
        manager: BudgetManager,
        task: AgentTask,
        profile: BudgetProfile,
        task_usage: Usage,
        run_usage: Usage,
    ) -> None:
        self._m = manager
        self.task = task
        self.profile = profile
        self._task_usage = task_usage
        self._run_usage = run_usage
        self._started = manager._monotonic()
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------------------------- read-only

    @property
    def task_usage(self) -> Usage:
        return self._task_usage

    @property
    def run_usage(self) -> Usage:
        return self._run_usage

    @property
    def model_call_timeout_s(self) -> int:
        return self.profile.model_call_timeout_s

    def elapsed_ms(self) -> int:
        return int((self._m._monotonic() - self._started) * 1000)

    # ---------------------------------------------------------------------------------------- checks

    def _deny(self, dimension: str, limit: int, used: int) -> BudgetExhaustedError:
        return self._m._exhausted(self.task, dimension, limit, used)

    def _check_time(self) -> None:
        elapsed = self.elapsed_ms()
        task_limit = self.profile.max_task_minutes * 60_000
        if self._task_usage.active_ms + elapsed > task_limit:
            raise self._deny("task_time", task_limit, self._task_usage.active_ms + elapsed)
        run_limit = self._m.budgets.run.max_active_hours * 3_600_000
        if self._run_usage.active_ms + elapsed > run_limit:
            raise self._deny("run_active_time", run_limit, self._run_usage.active_ms + elapsed)

    def check_start(self) -> None:
        """Before the task runs at all: nothing may already be exhausted."""
        with self._lock:
            self._check_time()
            run = self._m.budgets.run
            if self._run_usage.model_calls >= run.max_model_calls:
                raise self._deny("run_model_calls", run.max_model_calls, self._run_usage.model_calls)
            if self._run_usage.tokens >= run.max_tokens:
                raise self._deny("run_tokens", run.max_tokens, self._run_usage.tokens)
            if self._task_usage.model_calls >= self.profile.max_model_calls:
                raise self._deny(
                    "task_model_calls", self.profile.max_model_calls, self._task_usage.model_calls
                )

    def check_context_window(self, requested: int) -> None:
        """F-9c: a request may not ask for a larger context window than its profile allows."""
        if requested > self.profile.context_window:
            raise self._deny("context_window", self.profile.context_window, requested)

    def check_model_call(self, input_tokens: int, max_output_tokens: int) -> None:
        """Before a model call: per-call limits, task/run totals (reserving the full answer), time, RAM."""
        with self._lock:
            p, run = self.profile, self._m.budgets.run
            if max_output_tokens > p.max_output_tokens:
                raise self._deny("output_tokens", p.max_output_tokens, max_output_tokens)
            if input_tokens > p.max_input_tokens:
                raise self._deny("input_context", p.max_input_tokens, input_tokens)
            calls = self._task_usage.model_calls + 1
            if calls > p.max_model_calls:
                raise self._deny("task_model_calls", p.max_model_calls, calls)
            reserved = input_tokens + max_output_tokens
            if self._task_usage.tokens + reserved > p.max_task_tokens:
                raise self._deny("task_tokens", p.max_task_tokens, self._task_usage.tokens + reserved)
            if self._run_usage.model_calls + 1 > run.max_model_calls:
                raise self._deny("run_model_calls", run.max_model_calls, self._run_usage.model_calls + 1)
            if self._run_usage.tokens + reserved > run.max_tokens:
                raise self._deny("run_tokens", run.max_tokens, self._run_usage.tokens + reserved)
            self._check_time()
            free = self._m.free_memory_mb()
            needed = self._m.budgets.resources.min_free_ram_mb
            if free < needed:
                raise ResourceUnavailableError(
                    "resource.low_memory", f"{free} MB free; a model call needs at least {needed} MB (F-9n)"
                )

    def check_tool_call(self) -> None:
        with self._lock:
            calls = self._task_usage.tool_calls + 1
            if calls > self.profile.max_tool_calls:
                raise self._deny("task_tool_calls", self.profile.max_tool_calls, calls)
            self._check_time()

    # ------------------------------------------------------------------------------------- recording

    def _add(self, delta: Usage) -> None:
        self._m._persist(self.task, delta)
        self._task_usage = _sum(self._task_usage, delta)
        self._run_usage = _sum(self._run_usage, delta)

    def record_model_call(self, usage: ModelUsage) -> None:
        with self._lock:
            self._add(
                Usage(model_calls=1, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
            )

    def record_tool_call(self, bytes_written: int = 0) -> None:
        with self._lock:
            self._add(Usage(tool_calls=1, bytes_written=bytes_written))

    def close(self) -> None:
        """Add this execution's active time to the task and run (once)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._add(Usage(active_ms=self.elapsed_ms()))


def _sum(a: Usage, b: Usage) -> Usage:
    return Usage(
        model_calls=a.model_calls + b.model_calls,
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        tool_calls=a.tool_calls + b.tool_calls,
        bytes_written=a.bytes_written + b.bytes_written,
        active_ms=a.active_ms + b.active_ms,
    )
