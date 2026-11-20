"""Output guardrails (HC-01, Working Plan §10.1): applied to every agent output.

Order: size → secret scan → schema (contract) → input/output cross-checks → forbidden claims.

- Schema and cross-check failures and forbidden claims are ``invalid_output``: the executor may retry
  within F-10a, sending back the violation list.
- A secret in an output is ``policy_denied``: never retried automatically (F-10c).

Violation messages describe *where* and *which rule*, never the offending content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from devteam_core.contracts.agents import (
    AGENT_IO,
    AgentIO,
    ContractViolationError,
    check_output_against_input,
)
from devteam_core.contracts.base import NEVER_ASSUME, Sourced, SourceRef
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.statuses import AgentRole
from devteam_core.errors import DevTeamError
from devteam_core.logging import redact_text, secret_registry
from devteam_harness.guardrails.config import OutputRules
from devteam_harness.guardrails.secret_scan import secret_findings

MAX_VIOLATIONS = 20


class OutputRejectedError(DevTeamError):
    def __init__(self, category: ErrorCategory, code: str, violations: tuple[str, ...]) -> None:
        super().__init__(f"{code}: {len(violations)} violation(s)")
        self.category = category
        self.code = code
        self.violations = violations


def _agent_text(value: Any, location: str) -> Iterator[tuple[str, str]]:
    """(location, text) for every string the agent authored; quoted BRD facts/user answers excluded."""
    if isinstance(value, SourceRef):
        return
    if isinstance(value, Sourced) and value.provenance in NEVER_ASSUME:
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _agent_text(getattr(value, name), f"{location}.{name}" if location else name)
    elif isinstance(value, str):
        yield location, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _agent_text(item, f"{location}.{key}")
    elif isinstance(value, list | tuple | set | frozenset):
        for index, item in enumerate(value):
            yield from _agent_text(item, f"{location}[{index}]")


def _validation_messages(exc: ValidationError) -> tuple[str, ...]:
    messages = []
    for error in exc.errors(include_input=False, include_url=False)[:MAX_VIOLATIONS]:
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        messages.append(redact_text(f"{loc}: {error['msg']}", secret_registry.values())[:300])
    return tuple(messages)


class OutputGuard:
    def __init__(self, rules: OutputRules) -> None:
        self._rules = rules

    def check(self, role: AgentRole, given: AgentIO, raw: str | Mapping[str, Any]) -> AgentIO:
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        if len(text) > self._rules.max_output_chars:
            raise OutputRejectedError(
                ErrorCategory.INVALID_OUTPUT,
                "output.too_large",
                (f"output exceeds {self._rules.max_output_chars} characters",),
            )
        kinds = secret_findings(text)
        if kinds:
            raise OutputRejectedError(
                ErrorCategory.POLICY_DENIED,
                "output.secret_detected",
                (f"secret-like content: {', '.join(kinds)}",),
            )
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OutputRejectedError(
                    ErrorCategory.INVALID_OUTPUT, "output.not_json", (f"not valid JSON (line {exc.lineno})",)
                ) from None
        else:
            data = dict(raw)
        if not isinstance(data, dict):
            raise OutputRejectedError(
                ErrorCategory.INVALID_OUTPUT, "output.not_object", ("expected a JSON object",)
            )

        output_model = AGENT_IO[role][1]
        try:
            output = output_model.model_validate(data)
        except ValidationError as exc:
            raise OutputRejectedError(
                ErrorCategory.INVALID_OUTPUT, "output.schema", _validation_messages(exc)
            ) from None
        try:
            check_output_against_input(role, given, output)
        except ContractViolationError as exc:
            raise OutputRejectedError(
                ErrorCategory.INVALID_OUTPUT,
                "output.contract",
                (redact_text(str(exc), secret_registry.values())[:300],),
            ) from None

        claims = [
            f"forbidden claim '{pattern.id}' at {location}"
            for location, value in _agent_text(output, "")
            for pattern in self._rules.forbidden_claims
            if pattern.regex.search(value)
        ]
        if claims:
            raise OutputRejectedError(
                ErrorCategory.INVALID_OUTPUT, "output.forbidden_claim", tuple(claims[:MAX_VIOLATIONS])
            )
        return output
