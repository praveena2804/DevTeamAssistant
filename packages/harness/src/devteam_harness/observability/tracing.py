"""Local-only OpenTelemetry tracing (HC-11, HS-15; D-30 approved AP-007).

- Spans are exported **only** to the ``trace_spans`` table in the local SQLite state database by
  :class:`SqliteSpanExporter`. No other exporter is installed or configurable: the tracer provider is
  private to :class:`Tracer` (the global OpenTelemetry provider and ``OTEL_*`` environment variables
  are not used), and the OTLP exporter packages are not dependencies.
- Span attributes pass an **allowlist** of keys (IDs, names, counts, durations, outcome codes). Adding
  any other key raises at the call site, and the exporter filters again (defence in depth). String
  values must look like an identifier or code (no spaces, bounded length) and are run through
  redaction, so prompts, model output, document text, file contents, tokens and credentials cannot be
  stored as attributes. Exception messages are never recorded, only the exception type.
- Spans reuse the Part A trace IDs: a span opened while a ``devteam_core.tracing`` context is current
  becomes its child, and the span's own IDs become current inside it, so logs and audit rows carry the
  same trace ID.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)
from sqlalchemy.orm import Session, sessionmaker

from devteam_core import tracing
from devteam_core.logging import get_logger, redact_text, secret_registry
from devteam_state.repositories.execution import SpanRecord, add_spans

ALLOWED_ATTRIBUTES = frozenset(
    {
        "project_id",
        "run_id",
        "task_id",
        "agent.role",
        "operation_id",
        "stage",
        "attempt",
        "outcome",
        "disposition",
        "error.category",
        "error.code",
        "error.type",
        "policy.rule_id",
        "budget.dimension",
        "tool.name",
        "tool.version",
        "model.provider",
        "model.id",
        "model.slot",
        "prompt.id",
        "prompt.version",
        "tokens.input",
        "tokens.output",
        "duration_ms",
        "retries",
    }
)
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/@+-]{0,120}$")
REJECTED = "<rejected>"
_log = get_logger("devteam_harness.tracing")

AttributeValue = str | int | float | bool


class TraceAttributeError(ValueError):
    """A span attribute outside the allowlist or of an unsafe type (a programming error)."""


def clean_attribute(key: str, value: object) -> AttributeValue:
    """Return a storable value for an allowlisted key, or raise for a key that is not allowed."""
    if key not in ALLOWED_ATTRIBUTES:
        raise TraceAttributeError(f"span attribute '{key[:40]}' is not on the allowlist")
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        redacted = redact_text(value, secret_registry.values())
        return redacted if redacted == value and _SAFE_VALUE.fullmatch(value) else REJECTED
    raise TraceAttributeError(f"span attribute '{key}' must be a string, number or boolean")


def filter_attributes(attributes: Any) -> dict[str, AttributeValue]:
    """Exporter-side filter: drop unknown keys, re-check values (never raises)."""
    clean: dict[str, AttributeValue] = {}
    for key, value in dict(attributes or {}).items():
        try:
            clean[key] = clean_attribute(key, value)
        except TraceAttributeError:
            continue
    return clean


class SqliteSpanExporter(SpanExporter):
    """The only exporter: writes finished spans to the local ``trace_spans`` table."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        records = []
        for span in spans:
            ctx = span.context
            if ctx is None or span.start_time is None or span.end_time is None:
                continue
            attributes = filter_attributes(span.attributes)
            project = attributes.get("project_id")
            records.append(
                SpanRecord(
                    span_id=format(ctx.span_id, "016x"),
                    trace_id=format(ctx.trace_id, "032x"),
                    parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
                    name=span.name[:100],
                    project_id=project if isinstance(project, str) and project != REJECTED else None,
                    start_ts=datetime.fromtimestamp(span.start_time / 1e9, UTC),
                    end_ts=datetime.fromtimestamp(span.end_time / 1e9, UTC),
                    status=span.status.status_code.name.lower(),
                    attributes=attributes,
                )
            )
        try:
            with self._sessions.begin() as session:
                add_spans(session, records)
        except Exception as exc:  # tracing must never break the work it observes
            _log.warning("trace_export_failed", error_type=type(exc).__name__)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class SpanHandle:
    """What instrumented code may do with an open span: set allowlisted attributes."""

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def set(self, key: str, value: object) -> None:
        self._span.set_attribute(key, clean_attribute(key, value))


class Tracer:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._exporter = SqliteSpanExporter(sessions)
        self._processor = SimpleSpanProcessor(self._exporter)
        self._provider = TracerProvider(resource=Resource({"service.name": "devteam-harness"}))
        self._provider.add_span_processor(self._processor)
        self._tracer = self._provider.get_tracer("devteam_harness")

    @property
    def exporters(self) -> tuple[SpanExporter, ...]:
        """Every exporter attached to this tracer (inspected by the D-30 tests)."""
        processors = self._provider._active_span_processor._span_processors
        found: list[SpanExporter] = []
        for processor in processors:
            exporter = getattr(processor, "span_exporter", None)
            found.append(exporter if exporter is not None else processor)  # type: ignore[arg-type]
        return tuple(found)

    @contextmanager
    def span(self, name: str, **attributes: object) -> Iterator[SpanHandle]:
        clean = {key: clean_attribute(key, value) for key, value in attributes.items()}
        parent = tracing.current()
        if parent is not None:
            remote = SpanContext(
                trace_id=int(parent.trace_id, 16),
                span_id=int(parent.span_id, 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            otel_parent = set_span_in_context(NonRecordingSpan(remote), Context())
        else:
            otel_parent = Context()
        with self._tracer.start_as_current_span(
            name[:100],
            context=otel_parent,
            attributes=clean,
            record_exception=False,
            set_status_on_exception=False,
        ) as otel_span:
            sc = otel_span.get_span_context()
            own = tracing.TraceContext(
                trace_id=format(sc.trace_id, "032x"),
                span_id=format(sc.span_id, "016x"),
                parent_span_id=parent.span_id if parent else None,
            )
            with tracing.use_context(own):
                try:
                    yield SpanHandle(otel_span)
                except BaseException as exc:
                    otel_span.set_status(Status(StatusCode.ERROR))
                    otel_span.set_attribute("error.type", clean_attribute("error.type", type(exc).__name__))
                    raise

    def shutdown(self) -> None:
        self._provider.shutdown()


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "REJECTED",
    "SpanHandle",
    "SqliteSpanExporter",
    "TraceAttributeError",
    "Tracer",
    "clean_attribute",
    "filter_attributes",
]
