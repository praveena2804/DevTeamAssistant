"""HC-11 observability: local-only OpenTelemetry tracing (D-30)."""

from devteam_harness.observability.tracing import (
    ALLOWED_ATTRIBUTES,
    SpanHandle,
    SqliteSpanExporter,
    TraceAttributeError,
    Tracer,
    clean_attribute,
    filter_attributes,
)

__all__ = [
    "ALLOWED_ATTRIBUTES",
    "SpanHandle",
    "SqliteSpanExporter",
    "TraceAttributeError",
    "Tracer",
    "clean_attribute",
    "filter_attributes",
]
