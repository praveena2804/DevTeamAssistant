"""Retry policy (HC-12, HS-13; F-10 approved AP-008).

==========================  ==========  ======================================================
category                    retries     wait between attempts
==========================  ==========  ======================================================
``invalid_output``          F-10a (2)   none; the violations are sent back with the next attempt
``retryable`` (transient)   F-10b (2)   2 s, then 4 s ... capped at ``backoff_max_s`` (30 s), jittered
``policy_denied``           0           never retried (F-10c, fixed in code)
``budget_exhausted``        0           never retried (F-10c, fixed in code)
``resource_unavailable``    0 here      the run waits for resources instead (WAITING_FOR_RESOURCES)
``fatal``                   0
==========================  ==========  ======================================================
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass

from devteam_core.contracts.errors import ErrorCategory
from devteam_harness.budgets.config import RetryLimits

_JITTER_STEPS = 1_000


def _system_jitter() -> float:
    """A value in [0, 1). Not security-sensitive; ``secrets`` avoids a seeded global RNG."""
    return secrets.randbelow(_JITTER_STEPS) / _JITTER_STEPS


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    delay_s: float
    reason: str


class RetryPolicy:
    def __init__(self, limits: RetryLimits, *, jitter: Callable[[], float] = _system_jitter) -> None:
        self._limits = limits
        self._jitter = jitter

    def max_retries(self, category: ErrorCategory) -> int:
        if category is ErrorCategory.INVALID_OUTPUT:
            return self._limits.invalid_output
        if category is ErrorCategory.RETRYABLE:
            return self._limits.transient
        return 0  # F-10c and everything else: never automatic

    def backoff_s(self, retry_number: int) -> float:
        """Wait before retry ``retry_number`` (1-based): base x 2^(n-1), +0-25% jitter, never above max."""
        if retry_number < 1:
            raise ValueError("retry numbers start at 1")
        base = self._limits.backoff_base_s * (2 ** (retry_number - 1))
        jitter = 1.0 + 0.25 * min(max(self._jitter(), 0.0), 1.0)
        return float(min(self._limits.backoff_max_s, base * jitter))

    def decide(self, category: ErrorCategory, retries_done: int) -> RetryDecision:
        allowed = self.max_retries(category)
        if retries_done >= allowed:
            reason = "never retried automatically" if allowed == 0 else f"retry limit {allowed} reached"
            return RetryDecision(retry=False, delay_s=0.0, reason=reason)
        delay = self.backoff_s(retries_done + 1) if category is ErrorCategory.RETRYABLE else 0.0
        return RetryDecision(retry=True, delay_s=delay, reason=f"retry {retries_done + 1} of {allowed}")


__all__ = ["RetryDecision", "RetryPolicy"]
