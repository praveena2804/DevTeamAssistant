"""Trace-context propagation (HC-11 foundation, HS-01).

IDs follow the W3C Trace Context format (32-hex trace id, 16-hex span id, ``traceparent`` header), so the
OpenTelemetry API introduced in Part C (D-30) can adopt them without translation. The current context lives
in a ``ContextVar`` and is therefore isolated per thread and per asyncio task. Log lines pick it up
automatically (see :mod:`devteam_core.logging`).
"""

import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    sampled: bool = True


_current: ContextVar[TraceContext | None] = ContextVar("devteam_trace_context", default=None)


def _new_trace_id() -> str:
    value = secrets.token_hex(16)
    return value if value != _ZERO_TRACE else _new_trace_id()


def _new_span_id() -> str:
    value = secrets.token_hex(8)
    return value if value != _ZERO_SPAN else _new_span_id()


def current() -> TraceContext | None:
    return _current.get()


def new_root() -> TraceContext:
    return TraceContext(trace_id=_new_trace_id(), span_id=_new_span_id())


def child_of(parent: TraceContext) -> TraceContext:
    return TraceContext(
        trace_id=parent.trace_id,
        span_id=_new_span_id(),
        parent_span_id=parent.span_id,
        sampled=parent.sampled,
    )


@contextmanager
def use_context(ctx: TraceContext) -> Iterator[TraceContext]:
    """Make ``ctx`` current for the duration of the block."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextmanager
def span() -> Iterator[TraceContext]:
    """Open a child span of the current context, or a new root trace when none is active."""
    parent = _current.get()
    ctx = child_of(parent) if parent is not None else new_root()
    with use_context(ctx):
        yield ctx


def parse_traceparent(header: str | None) -> TraceContext | None:
    """Parse a W3C ``traceparent`` header. Invalid values return ``None`` (never raise)."""
    if not header:
        return None
    match = _TRACEPARENT_RE.fullmatch(header.strip().lower())
    if match is None or match.group("version") == "ff":
        return None
    trace_id, span_id = match.group("trace_id"), match.group("span_id")
    if trace_id == _ZERO_TRACE or span_id == _ZERO_SPAN:
        return None
    return TraceContext(trace_id=trace_id, span_id=span_id, sampled=bool(int(match.group("flags"), 16) & 1))


def format_traceparent(ctx: TraceContext) -> str:
    return f"00-{ctx.trace_id}-{ctx.span_id}-{'01' if ctx.sampled else '00'}"
