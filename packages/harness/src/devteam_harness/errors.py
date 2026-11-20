"""Categorised harness errors (HC-12). The category decides what the executor may do next.

Only ``retryable`` and ``invalid_output`` are ever retried automatically (F-10a/F-10b). Policy denials
and exhausted budgets are never retried (F-10c). Messages are short and never contain secrets, prompts
or document text.
"""

from __future__ import annotations

from devteam_core.contracts.errors import ErrorCategory
from devteam_core.errors import DevTeamError


class HarnessError(DevTeamError):
    category: ErrorCategory = ErrorCategory.FATAL

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TransientError(HarnessError):
    """Timeouts, connection resets, model busy (F-10b)."""

    category = ErrorCategory.RETRYABLE


class InvalidOutputError(HarnessError):
    """The model or agent output failed schema or guardrail checks (F-10a)."""

    category = ErrorCategory.INVALID_OUTPUT

    def __init__(self, code: str, message: str, violations: tuple[str, ...] = ()) -> None:
        super().__init__(code, message)
        self.violations = violations


class PolicyDeniedError(HarnessError):
    category = ErrorCategory.POLICY_DENIED

    def __init__(self, code: str, message: str, rule_id: str | None = None) -> None:
        super().__init__(code, message)
        self.rule_id = rule_id


class BudgetExhaustedError(HarnessError):
    category = ErrorCategory.BUDGET_EXHAUSTED

    def __init__(self, dimension: str, limit: int, used: int) -> None:
        super().__init__(
            f"budget.{dimension}", f"budget '{dimension}' exhausted (limit {limit}, would use {used})"
        )
        self.dimension = dimension
        self.limit = limit
        self.used = used


class ResourceUnavailableError(HarnessError):
    """Not enough free memory, or a required resource is missing; the run waits (WAITING_FOR_RESOURCES)."""

    category = ErrorCategory.RESOURCE_UNAVAILABLE


class FatalError(HarnessError):
    category = ErrorCategory.FATAL


def category_of(exc: BaseException) -> ErrorCategory:
    """Classify any exception. Anything the harness does not recognise is fatal (never retried)."""
    if isinstance(exc, HarnessError):
        return exc.category
    return ErrorCategory.FATAL


__all__ = [
    "BudgetExhaustedError",
    "FatalError",
    "HarnessError",
    "InvalidOutputError",
    "PolicyDeniedError",
    "ResourceUnavailableError",
    "TransientError",
    "category_of",
]
