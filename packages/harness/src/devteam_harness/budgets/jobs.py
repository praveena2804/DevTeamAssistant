"""Budgets for platform service jobs that call models (Part F plan §6.6): RAG indexing and retrieval,
evaluation runs. Agents never use these; their calls are budgeted per task (``TaskBudget``).

A ``JobBudget`` limits one job's model calls, tokens and wall-clock time, and refuses a call when free
memory is below the configured minimum (the call then waits for resources, as for agents).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from devteam_core.contracts.llm import ModelUsage
from devteam_harness.budgets.manager import free_ram_mb
from devteam_harness.errors import BudgetExhaustedError, ResourceUnavailableError


@dataclass(frozen=True, slots=True)
class JobLimits:
    max_model_calls: int
    max_tokens: int
    max_minutes: float
    model_call_timeout_s: int
    min_free_ram_mb: int
    max_context_window: int = 8_192


class JobBudget:
    """Live budget for one service job. Thread-safe. Same interface the gateway uses for ``TaskBudget``."""

    def __init__(
        self,
        limits: JobLimits,
        *,
        project_id: str | None,
        run_id: str | None = None,
        free_ram: Callable[[], int] = free_ram_mb,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._project_id = project_id
        self._run_id = run_id
        self._free_ram = free_ram
        self._monotonic = monotonic
        self._started = monotonic()
        self._calls = 0
        self._tokens = 0
        self._lock = threading.Lock()

    @property
    def project_id(self) -> str | None:
        return self._project_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def task_id(self) -> str | None:
        return None

    @property
    def model_call_timeout_s(self) -> int:
        return self.limits.model_call_timeout_s

    @property
    def calls(self) -> int:
        return self._calls

    def check_context_window(self, requested: int) -> None:
        if requested > self.limits.max_context_window:
            raise BudgetExhaustedError("context_window", self.limits.max_context_window, requested)

    def check_model_call(self, input_tokens: int, max_output_tokens: int) -> None:
        with self._lock:
            if self._calls + 1 > self.limits.max_model_calls:
                raise BudgetExhaustedError("job_model_calls", self.limits.max_model_calls, self._calls + 1)
            reserved = self._tokens + input_tokens + max_output_tokens
            if reserved > self.limits.max_tokens:
                raise BudgetExhaustedError("job_tokens", self.limits.max_tokens, reserved)
            elapsed_min = (self._monotonic() - self._started) / 60
            if elapsed_min > self.limits.max_minutes:
                raise BudgetExhaustedError("job_time", int(self.limits.max_minutes), int(elapsed_min))
            free = self._free_ram()
            if free < self.limits.min_free_ram_mb:
                raise ResourceUnavailableError(
                    "resource.low_memory",
                    f"{free} MB free; a model call needs at least {self.limits.min_free_ram_mb} MB",
                )

    def record_model_call(self, usage: ModelUsage) -> None:
        with self._lock:
            self._calls += 1
            self._tokens += usage.total


__all__ = ["JobBudget", "JobLimits"]
