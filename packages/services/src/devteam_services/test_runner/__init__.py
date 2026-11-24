"""Test Runner base (deterministic service; Part E plan §3.4; TS-06).

Runs a test recipe (``pytest_api``, ``pytest_db``, ``vitest_run``) in the sandbox and returns the counts
parsed from the JUnit report by the harness. A run "passes" only if the process exited 0 **and** the
report shows at least one executed test with no failures or errors; a missing or unreadable report is
never a pass. ``TestRun`` records, requirement evidence and the trivial-test detector arrive in Part M;
only this service (never an agent) will write them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from devteam_core.contracts.sandbox import SandboxExecutionStatus
from devteam_harness.errors import PolicyDeniedError
from devteam_harness.iam.principals import ServiceName, ServicePrincipal
from devteam_harness.sandbox.service import Evidence, ExecutionOutcome, ExecutionOwner, SandboxService

PRINCIPAL = ServicePrincipal(ServiceName.TEST_RUNNER).id
TEST_RECIPES = frozenset({"pytest_api", "pytest_db", "vitest_run"})


@dataclass(frozen=True, slots=True)
class TestRunResult:
    __test__ = False  # not a pytest class
    outcome: ExecutionOutcome

    @property
    def passed(self) -> bool:
        execution = self.outcome.execution
        return (
            execution.status is SandboxExecutionStatus.COMPLETED
            and execution.exit_code == 0
            and execution.tests is not None
            and execution.tests.clean
        )


class TestRunner:
    __test__ = False

    def __init__(self, sandbox: SandboxService) -> None:
        self._sandbox = sandbox

    def run(
        self,
        recipe: str,
        *,
        files: Mapping[str, bytes],
        owner: ExecutionOwner,
        evidence: Evidence,
        select: str = "all",
    ) -> TestRunResult:
        if recipe not in TEST_RECIPES:
            raise PolicyDeniedError(
                "recipe.not_a_test_recipe", f"{recipe} is not a test recipe", "deny.recipe"
            )
        args = {"select": select} if recipe == "pytest_api" else {}
        outcome = self._sandbox.execute(
            recipe=recipe, args=args, files=files, owner=owner, principal_id=PRINCIPAL, evidence=evidence
        )
        return TestRunResult(outcome)


__all__ = ["PRINCIPAL", "TEST_RECIPES", "TestRunResult", "TestRunner"]
