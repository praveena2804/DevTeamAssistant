"""Loading ``config/budgets.yaml`` (HC-05 budgets, HC-12 retries; F-9, F-10 approved AP-008).

Fail closed: a missing or malformed file, an unknown key, or a value outside the approved range stops
loading. The ranges below are the owner-approved F-9/F-10 min-max values; the YAML chooses a value
inside them. Retries after a policy denial or an exhausted budget (F-10c) are fixed at 0 in code and
cannot be configured: any such key is an unknown key and is rejected.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from devteam_core.contracts.tasks import BudgetProfileName
from devteam_core.errors import ConfigError

FILE = "budgets.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetProfile(_Strict):
    """Per-task limits (one profile per ``AgentTask.budget_profile``, F-11d)."""

    max_output_tokens: int = Field(ge=128, le=2_048)  # F-9a
    max_input_tokens: int = Field(ge=1_024, le=8_064)  # F-9b (also bounded by the window below)
    context_window: int = Field(ge=2_048, le=8_192)  # F-9c
    model_call_timeout_s: int = Field(ge=30, le=900)  # F-9d
    max_model_calls: int = Field(ge=1, le=50)  # F-9e
    max_task_tokens: int = Field(ge=4_096, le=200_000)  # F-9f
    max_task_minutes: int = Field(ge=1, le=120)  # F-9g
    max_tool_calls: int = Field(ge=10, le=1_000)  # F-9h

    @model_validator(mode="after")
    def _fits_window(self) -> Self:
        if self.max_input_tokens + self.max_output_tokens > self.context_window:
            raise ValueError("max_input_tokens + max_output_tokens must fit inside context_window (F-9b)")
        return self


class RunLimits(_Strict):
    max_active_hours: int = Field(ge=1, le=48)  # F-9k
    max_tokens: int = Field(ge=100_000, le=5_000_000)  # F-9l
    max_model_calls: int = Field(ge=50, le=3_000)  # F-9m


class ResourceLimits(_Strict):
    min_free_ram_mb: int = Field(ge=256, le=8_192)  # F-9n


class RetryLimits(_Strict):
    invalid_output: int = Field(ge=0, le=3)  # F-10a
    transient: int = Field(ge=0, le=5)  # F-10b
    backoff_base_s: float = Field(ge=0.1, le=10)
    backoff_max_s: float = Field(ge=1, le=30)  # "max wait 30 s"
    task_reattempts: int = Field(ge=0, le=3)  # F-10d

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.backoff_base_s > self.backoff_max_s:
            raise ValueError("backoff_base_s must not exceed backoff_max_s")
        return self


class _BudgetFile(_Strict):
    version: Literal[1]
    profiles: dict[BudgetProfileName, BudgetProfile] = Field(min_length=1)
    run: RunLimits
    resources: ResourceLimits
    retries: RetryLimits

    @model_validator(mode="after")
    def _default_profile(self) -> Self:
        if "default" not in self.profiles:
            raise ValueError("a 'default' budget profile is required")
        for name, profile in self.profiles.items():
            if profile.max_task_tokens > self.run.max_tokens:
                raise ValueError(f"profile '{name}' allows more tokens per task than the whole run")
            if profile.max_model_calls > self.run.max_model_calls:
                raise ValueError(f"profile '{name}' allows more model calls per task than the whole run")
        return self


@dataclass(frozen=True, slots=True)
class Budgets:
    profiles: Mapping[str, BudgetProfile]
    run: RunLimits
    resources: ResourceLimits
    retries: RetryLimits
    digest: str

    def profile(self, name: str) -> BudgetProfile:
        try:
            return self.profiles[name]
        except KeyError:
            raise ConfigError(f"unknown budget profile '{name}'") from None


def parse_budgets(raw: bytes, *, source: str = FILE) -> Budgets:
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
        parsed = _BudgetFile.model_validate(data)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{source} is not valid YAML: {type(exc).__name__}") from None
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        location = ".".join(str(p) for p in first["loc"]) or "<root>"
        raise ConfigError(f"{source} is invalid at {location}: {first['msg']}") from None
    return Budgets(
        profiles=MappingProxyType(dict(parsed.profiles)),
        run=parsed.run,
        resources=parsed.resources,
        retries=parsed.retries,
        digest=hashlib.sha256(raw).hexdigest(),
    )


def load_budgets(config_dir: Path) -> Budgets:
    path = config_dir / FILE
    try:
        raw = path.read_bytes()
    except OSError:
        raise ConfigError(f"{FILE} is missing: the harness does not run without budgets") from None
    return parse_budgets(raw)
