"""HC-18 rate limiting: in-memory token buckets for API routes (Part B). Gateway limits arrive in Part F."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token bucket per key. ``capacity`` requests per ``period_s``, refilled continuously."""

    def __init__(
        self, *, capacity: int, period_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if capacity < 1 or period_s <= 0:
            raise ValueError("capacity must be >= 1 and period_s > 0")
        self.capacity = capacity
        self.rate = capacity / period_s
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str) -> tuple[bool, float]:
        """Take one token. Returns ``(allowed, retry_after_seconds)``."""
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.capacity), updated=now)
                self._buckets[key] = bucket
            bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
            bucket.updated = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True, 0.0
            return False, (1 - bucket.tokens) / self.rate


__all__ = ["RateLimiter"]
