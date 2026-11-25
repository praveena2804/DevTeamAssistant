"""Scripted fake provider (tests and the Part C demonstration only; ``Provider.FAKE``).

Each call consumes the next scripted step: a string answer, an exception to raise, or a callable that
receives the request. It records every request it receives, so tests can assert exactly what reached
the "model" (for example that no secret or unwrapped document text did). ``delay_s`` simulates a slow
model for timeout tests; ``concurrent_peak`` records the highest number of simultaneous calls.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Union

from devteam_core.contracts.llm import ModelRequest, Provider
from devteam_harness.errors import FatalError
from devteam_harness.llm_gateway.providers import CallTimeouts, ProviderReply

Step = Union[str, BaseException, Callable[[ModelRequest], str]]  # noqa: UP007 - runtime alias


class FakeProvider:
    name = Provider.FAKE

    def __init__(self, script: Iterable[Step] = (), *, model_id: str = "fake-model", delay_s: float = 0.0):
        self._script: deque[Step] = deque(script)
        self.model_id = model_id
        self.delay_s = delay_s
        self.requests: list[ModelRequest] = []
        self._lock = threading.Lock()
        self._active = 0
        self.concurrent_peak = 0

    def add(self, *steps: Step) -> None:
        with self._lock:
            self._script.extend(steps)

    def generate(
        self, request: ModelRequest, *, model: str | None = None, timeouts: CallTimeouts | None = None
    ) -> ProviderReply:
        with self._lock:
            self.requests.append(request)
            self._active += 1
            self.concurrent_peak = max(self.concurrent_peak, self._active)
            step = self._script.popleft() if self._script else None
        try:
            if self.delay_s:
                time.sleep(self.delay_s)
            if step is None:
                raise FatalError("fake.script_exhausted", "the fake provider has no scripted answer left")
            if isinstance(step, BaseException):
                raise step
            text = step(request) if callable(step) else step
            prompt_chars = sum(len(m.content) for m in request.messages)
            return ProviderReply(
                text=text,
                input_tokens=math.ceil(prompt_chars / 4),
                output_tokens=max(1, math.ceil(len(text) / 4)),
                model_id=model or self.model_id,
            )
        finally:
            with self._lock:
                self._active -= 1

    def close(self) -> None:
        return None


__all__ = ["FakeProvider", "Step"]
