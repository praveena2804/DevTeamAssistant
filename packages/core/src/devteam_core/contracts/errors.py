"""Error categories (HC-12) and the error reference carried in state and tool results.

The category decides what the harness may do next. Automatic retry is permitted **only** for
``retryable`` and ``invalid_output`` (F-10a/F-10b). A policy denial or exhausted budget is never
retried automatically (F-10c, fixed and not configurable).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

from devteam_core.contracts._base import AwareDatetime, Contract, ShortText, TaskId


class ErrorCategory(StrEnum):
    RETRYABLE = "retryable"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_EXHAUSTED = "budget_exhausted"
    POLICY_DENIED = "policy_denied"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    FATAL = "fatal"


_AUTO_RETRYABLE = frozenset({ErrorCategory.RETRYABLE, ErrorCategory.INVALID_OUTPUT})


def is_auto_retryable(category: ErrorCategory) -> bool:
    """Whether the harness may retry automatically. Policy and budget outcomes never are (F-10c)."""
    return category in _AUTO_RETRYABLE


ErrorCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$", max_length=64)]


class ErrorRef(Contract):
    """A short, already-redacted description of an error. Never contains secrets or document text."""

    category: ErrorCategory
    code: ErrorCode
    message: ShortText
    task_id: TaskId | None = None
    occurred_at: AwareDatetime

    @property
    def auto_retryable(self) -> bool:
        return is_auto_retryable(self.category)
