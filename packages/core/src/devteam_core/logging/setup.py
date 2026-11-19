"""Structured JSON logging (HC-10, HS-01).

Every line carries ``ts``, ``level``, ``event``, ``logger`` and the correlation fields ``correlation_id``,
``project_id``, ``run_id``, ``task_id``, ``trace_id``, ``span_id`` and ``principal`` (``null`` when not
bound), as required by Working Plan §14. Standard-library loggers (uvicorn, alembic, …) go through the same
pipeline, including redaction.
"""

import logging
import logging.handlers
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

import structlog

from devteam_core import tracing
from devteam_core.config.settings import Settings
from devteam_core.logging.redaction import redact_event

CORRELATION_FIELDS = ("correlation_id", "project_id", "run_id", "task_id", "principal")
_HANDLER_MARKER = "_devteam_handler"


def add_correlation_fields(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    ctx = tracing.current()
    event_dict.setdefault("trace_id", ctx.trace_id if ctx else None)
    event_dict.setdefault("span_id", ctx.span_id if ctx else None)
    for field in CORRELATION_FIELDS:
        event_dict.setdefault(field, None)
    return event_dict


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        add_correlation_fields,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def _formatter(renderer: Any) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redact_event,  # always the last step before rendering
            renderer,
        ],
    )


def configure_logging(settings: Settings, *, stream: IO[str] | None = None) -> Path | None:
    """Configure structlog + stdlib logging. Idempotent. Returns the log file path when file logging is on."""
    level = logging.getLevelName(settings.logging.level)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()

    console_renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.logging.console_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(_formatter(console_renderer))
    setattr(console, _HANDLER_MARKER, True)
    root.addHandler(console)

    log_file: Path | None = None
    if settings.logging.file_enabled:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = settings.logs_dir / settings.logging.file_name
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=settings.logging.file_max_bytes,
            backupCount=settings.logging.file_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(_formatter(structlog.processors.JSONRenderer()))
        setattr(file_handler, _HANDLER_MARKER, True)
        root.addHandler(file_handler)

    root.setLevel(level)
    structlog.configure(
        processors=[*_shared_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return log_file


def get_logger(name: str | None = None) -> Any:
    return structlog.stdlib.get_logger(name)


def bind_context(**fields: Any) -> None:
    """Bind correlation fields (project_id, run_id, task_id, principal, correlation_id) for this context."""
    structlog.contextvars.bind_contextvars(**fields)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


@contextmanager
def bound_context(**fields: Any) -> Iterator[None]:
    """Bind fields for the duration of a block, restoring the previous values afterwards."""
    tokens = structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
