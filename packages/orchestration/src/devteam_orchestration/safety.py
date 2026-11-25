"""Local-only guard for the LangGraph stack (D-30, Part D plan §8).

``langgraph`` depends on ``langchain-core``, which depends on ``langsmith``: a client that can send run
traces to a cloud service when tracing is switched on through environment variables. The platform
keeps every trace local (D-30), so:

* :func:`cloud_tracing_settings` reports any tracing/endpoint variable that is set;
* :func:`ensure_local_only` **refuses** to continue when one of them enables cloud tracing or points at
  an endpoint, and otherwise forces the tracing switches off for this process, so a later library
  default cannot turn them on.

The worker calls :func:`ensure_local_only` before it imports or compiles any graph (fail closed).
Values of key variables are never echoed back: a report names the variable only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

# Variables that switch LangSmith / LangChain tracing on.
TRACING_SWITCHES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)
# Variables that only matter when tracing could reach a remote service.
REMOTE_SETTINGS = (
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_ENDPOINT",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_OTEL_ENABLED",
)
# Part F (KI-045): ChromaDB brings the OpenTelemetry OTLP exporter as a dependency. It is never configured by
# the platform; any variable that could configure an OpenTelemetry exporter or endpoint is refused.
OTEL_PREFIXES = ("OTEL_EXPORTER_", "OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER")
_OFF = {"", "0", "false", "no", "off"}


class CloudTracingConfiguredError(RuntimeError):
    """Cloud tracing is configured in the environment; the platform refuses to run (D-30)."""

    def __init__(self, variables: tuple[str, ...]) -> None:
        self.variables = variables
        super().__init__(
            "cloud tracing is configured through environment variable(s) "
            + ", ".join(variables)
            + "; DevTeam AI keeps traces local only (D-30). Unset them and start again."
        )


@dataclass(frozen=True, slots=True)
class TracingReport:
    enabled_switches: tuple[str, ...]
    remote_settings: tuple[str, ...]

    @property
    def violations(self) -> tuple[str, ...]:
        return self.enabled_switches + self.remote_settings


def cloud_tracing_settings(environ: Mapping[str, str] | None = None) -> TracingReport:
    env = os.environ if environ is None else environ
    enabled = tuple(name for name in TRACING_SWITCHES if env.get(name, "").strip().lower() not in _OFF)
    remote = tuple(name for name in REMOTE_SETTINGS if env.get(name, "").strip())
    remote += tuple(
        sorted(name for name in env if name.upper().startswith(OTEL_PREFIXES) and str(env[name]).strip())
    )
    return TracingReport(enabled_switches=enabled, remote_settings=remote)


def ensure_local_only(environ: MutableMapping[str, str] | None = None) -> None:
    """Fail closed when cloud tracing is configured; otherwise pin every tracing switch to ``false``."""
    env = os.environ if environ is None else environ
    report = cloud_tracing_settings(env)
    if report.violations:
        raise CloudTracingConfiguredError(report.violations)
    for name in TRACING_SWITCHES:
        env[name] = "false"
