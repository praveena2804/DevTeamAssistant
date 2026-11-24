"""Parse test reports produced inside the sandbox (Test Runner base; Part E plan §3.4; TS-06).

Reports are **untrusted data** written by generated code. The parser therefore:

- refuses any document with a DTD or entity declaration (``<!DOCTYPE``, ``<!ENTITY``) before parsing, so
  entity-expansion and external-entity attacks cannot run;
- caps the input size and the number of test cases kept;
- derives counts from the test cases it actually sees (it never trusts a suite's ``tests=`` attribute
  alone), so a report cannot claim passes that did not run.

``TestRun`` records (runner-only writes, requirement evidence) arrive in Part M; Part E stores the counts on
the ``SandboxExecution`` and in ``TemplateVerification``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from devteam_core.contracts.sandbox import TestCounts

MAX_REPORT_BYTES = 5 * 1024 * 1024
MAX_CASES = 5000
_FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class ReportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    outcome: str  # passed | failed | error | skipped
    requirements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JUnitReport:
    counts: TestCounts
    cases: tuple[CaseResult, ...] = field(default_factory=tuple)


def _safe_root(data: bytes) -> ET.Element:
    if len(data) > MAX_REPORT_BYTES:
        raise ReportError("test report too large")
    if _FORBIDDEN.search(data):
        raise ReportError("test report contains a DTD or entity declaration")
    try:
        return ET.fromstring(data)  # noqa: S314 - DTD/entities refused above; size capped
    except ET.ParseError as exc:
        raise ReportError(f"test report is not valid XML ({exc.code})") from None


def parse_junit(data: bytes) -> JUnitReport:
    root = _safe_root(data)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ReportError("not a JUnit report")
    cases: list[CaseResult] = []
    for case in root.iter("testcase"):
        if len(cases) >= MAX_CASES:
            break
        children = {child.tag for child in case}
        if "error" in children:
            outcome = "error"
        elif "failure" in children:
            outcome = "failed"
        elif "skipped" in children:
            outcome = "skipped"
        else:
            outcome = "passed"
        requirements = tuple(
            prop.get("value", "")
            for prop in case.iter("property")
            if prop.get("name") == "req" and re.fullmatch(r"(FR|NFR|BR)-[0-9]{3,4}", prop.get("value", ""))
        )
        name = f"{case.get('classname', '')}::{case.get('name', '')}"[:300]
        cases.append(CaseResult(name=name, outcome=outcome, requirements=requirements))
    count = {o: sum(c.outcome == o for c in cases) for o in ("passed", "failed", "error", "skipped")}
    return JUnitReport(
        counts=TestCounts(
            tests=len(cases),
            passed=count["passed"],
            failed=count["failed"],
            errors=count["error"],
            skipped=count["skipped"],
        ),
        cases=tuple(cases),
    )


def parse_coverage(data: bytes) -> float | None:
    """Line coverage (0-100) from a Cobertura report, or ``None`` if absent or unreadable."""
    try:
        root = _safe_root(data)
    except ReportError:
        return None
    rate = root.get("line-rate")
    try:
        return round(float(rate) * 100, 1) if rate is not None else None
    except ValueError:
        return None


__all__ = ["CaseResult", "JUnitReport", "ReportError", "parse_coverage", "parse_junit"]
