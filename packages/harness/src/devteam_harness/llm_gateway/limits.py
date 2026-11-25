"""Per-role and per-service model-call rate limits (HC-18; ``config/rate_limits.yaml``; Part F plan §6.5).

A call over its limit **waits** for the bucket to refill when that wait is within ``max_wait_s``;
otherwise it raises ``ResourceUnavailableError("rate.limited")`` (the task waits for resources). A rate
limit is never a recovery trigger and never silently drops a call.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from devteam_core.errors import ConfigError
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.ratelimit import RateLimiter


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Limit(_Strict):
    calls_per_minute: int = Field(ge=1, le=6_000)
    burst: int = Field(ge=1, le=1_000)


class RateLimitConfig(_Strict):
    schema_version: Literal[1] = 1
    max_wait_s: float = Field(default=30, ge=0, le=600)
    agents: dict[str, Limit]
    services: dict[str, Limit]


def load_rate_limits(config_dir: Path) -> RateLimitConfig:
    path = config_dir / "rate_limits.yaml"
    if not path.is_file():
        raise ConfigError(f"rate limits not found: {path}")
    try:
        config = RateLimitConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise ConfigError(f"rate_limits.yaml is invalid: {exc.error_count()} problem(s)") from None
    if "default" not in config.agents or "default" not in config.services:
        raise ConfigError("rate_limits.yaml needs a 'default' entry under agents and services")
    return config


class RateLimits:
    def __init__(
        self,
        config: RateLimitConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._clock = clock
        self._sleep = sleep
        self._limiters: dict[str, RateLimiter] = {}

    def _limiter(self, kind: str, name: str) -> tuple[str, RateLimiter]:
        table = self.config.agents if kind == "agent" else self.config.services
        limit = table.get(name) or table["default"]
        key = f"{kind}:{name}"
        if key not in self._limiters:
            self._limiters[key] = RateLimiter(
                capacity=limit.burst, period_s=limit.burst * 60.0 / limit.calls_per_minute, clock=self._clock
            )
        return key, self._limiters[key]

    def acquire(self, kind: Literal["agent", "service"], name: str) -> float:
        """Take one call slot; returns how long it waited. Raises ``rate.limited`` if the wait is too long."""
        key, limiter = self._limiter(kind, name)
        waited = 0.0
        while True:
            allowed, retry_after = limiter.try_acquire(key)
            if allowed:
                return waited
            if waited + retry_after > self.config.max_wait_s:
                raise ResourceUnavailableError(
                    "rate.limited", f"model-call rate limit for {kind} '{name}' reached; waiting for capacity"
                )
            self._sleep(retry_after)
            waited += retry_after


__all__ = ["Limit", "RateLimitConfig", "RateLimits", "load_rate_limits"]
