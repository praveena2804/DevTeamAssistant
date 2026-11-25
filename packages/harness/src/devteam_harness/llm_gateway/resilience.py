"""Gateway resilience helpers (GW-06; Part F plan §5 F.2, §6.2). Pure, thread-safe, no I/O.

* :data:`RECOVERABLE`: the resource failures that move a call to the next recovery rung (reduced context,
  then a smaller model). Invalid output, policy refusals and rate limits are **never** recovery triggers.
* :class:`CircuitBreakers`: per model, open after N consecutive resource failures (or slow calls); an open
  model is skipped until a cool-down passes, then one trial call is allowed (half-open).
* :class:`TokenCalibrator`: learns a model's characters-per-token from **cold** calls only (a warm call's
  ``prompt_eval_count`` can omit tokens reused from Ollama's KV cache) and never estimates below the
  conservative base estimate.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from devteam_harness.budgets.manager import estimate_tokens

RECOVERABLE = frozenset(
    {"model.oom", "model.load_failed", "model.timeout", "model.ram", "model.circuit_open"}
)


@dataclass
class _Breaker:
    failures: int = 0
    opened_at: float | None = None


class CircuitBreakers:
    def __init__(
        self, *, threshold: int = 3, cooldown_s: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if threshold < 1 or cooldown_s <= 0:
            raise ValueError("threshold >= 1 and cooldown_s > 0")
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._state: dict[str, _Breaker] = {}
        self._lock = threading.Lock()

    def allows(self, model: str) -> bool:
        with self._lock:
            state = self._state.get(model)
            if state is None or state.opened_at is None:
                return True
            if self._clock() - state.opened_at >= self.cooldown_s:
                state.opened_at = None  # half-open: one trial call
                state.failures = self.threshold - 1
                return True
            return False

    def failure(self, model: str) -> None:
        with self._lock:
            state = self._state.setdefault(model, _Breaker())
            state.failures += 1
            if state.failures >= self.threshold:
                state.opened_at = self._clock()

    def success(self, model: str) -> None:
        with self._lock:
            self._state.pop(model, None)

    def is_open(self, model: str) -> bool:
        with self._lock:
            state = self._state.get(model)
            return state is not None and state.opened_at is not None


class TokenCalibrator:
    """Characters per token, per model, learned from cold calls; bounded to [1.5, 6]."""

    MIN_CHARS_PER_TOKEN, MAX_CHARS_PER_TOKEN = 1.5, 6.0

    def __init__(self) -> None:
        self._ratio: dict[str, float] = {}
        self._lock = threading.Lock()

    def observe(self, model: str, chars: int, prompt_tokens: int, *, cold: bool) -> None:
        if not cold or chars <= 0 or prompt_tokens <= 0:
            return
        ratio = min(self.MAX_CHARS_PER_TOKEN, max(self.MIN_CHARS_PER_TOKEN, chars / prompt_tokens))
        with self._lock:
            old = self._ratio.get(model)
            self._ratio[model] = (
                ratio if old is None else min(old, (old + ratio) / 2)
            )  # keep the cautious side

    def estimate(self, model: str, text: str) -> int:
        base = estimate_tokens(text)
        with self._lock:
            ratio = self._ratio.get(model)
        if ratio is None:
            return base
        return max(base, math.ceil(len(text) / ratio))


__all__ = ["RECOVERABLE", "CircuitBreakers", "TokenCalibrator"]
