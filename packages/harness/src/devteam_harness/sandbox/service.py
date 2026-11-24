"""Sandbox execution service: one recipe, end to end (HC-14; Part E plan §3.3).

``execute`` runs, in order:

1. validate the recipe and its arguments (unknown recipe or argument → ``PolicyDeniedError``, nothing runs);
2. **Resource Guard**: LLM/sandbox lease, Ollama unloaded, enough free memory (else ``waiting for
   resources``: nothing runs and nothing is recorded);
3. connect to the WSL engine over mutual TLS and check the image lock (else **Blocked**: recorded, nothing
   runs, never a host fallback);
4. run in a disposable container (:class:`SandboxRunner`);
5. parse the JUnit / coverage reports (untrusted data, safely parsed);
6. write evidence (stdout, stderr, collected outputs) under the run's evidence folder, never into logs,
   audit or traces;
7. store the validated ``SandboxExecution`` and audit ``sandbox.executed`` / ``sandbox.blocked`` with
   metadata only (no output text).
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.config import Settings
from devteam_core.contracts.sandbox import SandboxExecution, SandboxExecutionStatus, TestCounts
from devteam_core.ids import new_id
from devteam_harness import audit
from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.sandbox.engine import Engine, connect
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.guard import ModelResidency, ResourceGuard
from devteam_harness.sandbox.images import ImageLock
from devteam_harness.sandbox.recipes import get_recipe
from devteam_harness.sandbox.results import JUnitReport, ReportError, parse_coverage, parse_junit
from devteam_harness.sandbox.runner import CancelSignal, RunRequest, RunResult, SandboxRunner
from devteam_harness.sandbox.spec import ceiling_of, effective_limits
from devteam_harness.secrets import SecretsManager
from devteam_state.repositories import sandbox as repo


@dataclass(frozen=True, slots=True)
class ExecutionOwner:
    """Exactly one of project (a run's work) or template verification (platform work)."""

    project_id: str | None = None
    run_id: str | None = None
    verification_id: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """Where evidence files go: ``root / prefix / <execution_id> / ...``; ``prefix`` is recorded as-is."""

    root: Path
    prefix: str


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    execution: SandboxExecution
    report: JUnitReport | None
    coverage_percent: float | None
    outputs: dict[str, bytes]


class SandboxService:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        secrets: SecretsManager,
        *,
        models: ModelResidency | None = None,
        clock: Clock = system_clock,
        engine_factory: Callable[[], Engine] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._clock = clock
        sandbox = settings.sandbox
        self._guard = ResourceGuard(
            ResourceLease(sessions, ttl_s=sandbox.lock_ttl_s, wait_s=sandbox.lock_wait_s, clock=clock),
            min_free_ram_mb=sandbox.min_free_ram_mb,
            models=models,
            unload_models=sandbox.unload_models_before_run,
        )
        self._engine_factory = engine_factory or (lambda: connect(settings, secrets))

    def execute(
        self,
        *,
        recipe: str,
        args: Mapping[str, Any] | None,
        files: Mapping[str, bytes],
        owner: ExecutionOwner,
        principal_id: str,
        evidence: Evidence,
        cancel: CancelSignal | None = None,
        wait_s: float | None = None,
    ) -> ExecutionOutcome:
        spec = get_recipe(recipe).spec(args)
        ceiling = ceiling_of(self._settings.sandbox.limits)
        limits = effective_limits(
            ceiling, ceiling.model_copy(update={"wall_clock_s": min(spec.timeout_s, ceiling.wall_clock_s)})
        )
        execution_id = new_id("sbx", self._clock)
        started = self._clock.now()
        handle = self._guard.acquire(wait_s=wait_s)  # ResourceUnavailableError: nothing ran
        try:
            try:
                with self._engine_factory() as engine:
                    lock = ImageLock.load(self._settings.sandbox_image_lock_path)
                    result = SandboxRunner(engine, lock, clock=self._clock).run(
                        RunRequest(
                            image=spec.image,
                            argv=spec.argv,
                            workdir=spec.workdir,
                            env=spec.env,
                            files=files,
                            limits=limits,
                            execution_id=execution_id,
                            labels=_labels(owner),
                            cancel=cancel,
                        )
                    )
            except SandboxBlockedError as exc:
                blocked = SandboxExecution(
                    execution_id=execution_id,
                    project_id=owner.project_id,
                    run_id=owner.run_id,
                    verification_id=owner.verification_id,
                    recipe=spec.recipe,
                    image=f"devteam-sandbox/{spec.image}",
                    limits=limits,
                    status=SandboxExecutionStatus.BLOCKED,
                    duration_ms=0,
                    reason=f"{exc.code}: {exc.message}"[:2000],
                    started_at=started,
                    finished_at=self._clock.now(),
                )
                self._record(blocked, principal_id, action="sandbox.blocked", outcome="denied")
                return ExecutionOutcome(blocked, None, None, {})
        finally:
            handle.release()
        report, coverage, reason = _reports(result, spec.reports_tests)
        paths = _write_evidence(evidence, execution_id, result)
        execution = SandboxExecution(
            execution_id=execution_id,
            project_id=owner.project_id,
            run_id=owner.run_id,
            verification_id=owner.verification_id,
            recipe=spec.recipe,
            image=f"devteam-sandbox/{spec.image}",
            image_digest=result.image_id,
            engine_version=result.engine_version[:40] or None,
            limits=limits,
            status=result.status,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            peak_memory_mb=result.peak_memory_mb,
            tests=report.counts if report is not None else None,
            outputs=paths,
            reason=_join(result.reason, reason),
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        self._record(
            execution,
            principal_id,
            action="sandbox.executed",
            outcome="success" if result.status is SandboxExecutionStatus.COMPLETED else "error",
        )
        return ExecutionOutcome(execution, report, coverage, result.outputs)

    def engine_status(self) -> dict[str, Any]:
        """Is the WSL engine usable right now? Metadata only (for the doctor-style status the API shows)."""
        try:
            with self._engine_factory() as engine:
                info = engine.info
            return {"reachable": True, "version": info.version, "operating_system": info.operating_system}
        except SandboxBlockedError as exc:
            return {"reachable": False, "reason": f"{exc.code}: {exc.message}"}

    def sweep(self) -> tuple[int, int] | None:
        """Remove containers and volumes left by a crashed run. ``None`` if the engine is not reachable."""
        try:
            with self._engine_factory() as engine:
                return engine.sweep()
        except SandboxBlockedError:
            return None

    def _record(self, execution: SandboxExecution, principal_id: str, *, action: str, outcome: str) -> None:
        tests: TestCounts | None = execution.tests
        details: dict[str, Any] = {
            "execution_id": execution.execution_id,
            "recipe": execution.recipe,
            "status": execution.status.value,
            "exit_code": execution.exit_code,
            "duration_ms": execution.duration_ms,
            "peak_memory_mb": execution.peak_memory_mb,
            "image_digest": execution.image_digest,
            "verification_id": execution.verification_id,
            "run_id": execution.run_id,
        }
        if tests is not None:
            details["tests"] = tests.model_dump(exclude={"schema_version"})
        with self._sessions.begin() as session:
            repo.record_execution(session, execution)
            audit.record(
                session,
                action=action,
                outcome=outcome,  # type: ignore[arg-type]
                principal_id=principal_id,
                project_id=execution.project_id,
                target_type="sandbox_execution",
                target_id=execution.execution_id,
                details=details,
            )


def _labels(owner: ExecutionOwner) -> dict[str, str]:
    labels = {}
    if owner.project_id:
        labels["devteam.sandbox.project_id"] = owner.project_id
    if owner.verification_id:
        labels["devteam.sandbox.verification_id"] = owner.verification_id
    return labels


def _reports(result: RunResult, reports_tests: bool) -> tuple[JUnitReport | None, float | None, str | None]:
    report: JUnitReport | None = None
    reason: str | None = None
    if reports_tests and result.status in {SandboxExecutionStatus.COMPLETED, SandboxExecutionStatus.FAILED}:
        raw = result.outputs.get("junit.xml")
        if raw is None:
            reason = "no test report was produced"
        else:
            try:
                report = parse_junit(raw)
            except ReportError as exc:
                reason = f"test report rejected: {exc}"
    coverage = parse_coverage(result.outputs["coverage.xml"]) if "coverage.xml" in result.outputs else None
    return report, coverage, reason


def _join(*parts: str | None) -> str | None:
    text = "; ".join(p for p in parts if p)
    return text[:2000] or None


def _write_evidence(evidence: Evidence, execution_id: str, result: RunResult) -> tuple[str, ...]:
    """Store stdout/stderr and collected outputs; return their workspace-relative paths."""
    base_rel = posixpath.join(evidence.prefix, execution_id)
    base = evidence.root.joinpath(*base_rel.split("/"))
    base.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, data in (("stdout.txt", result.stdout), ("stderr.txt", result.stderr)):
        (base / name).write_bytes(data)
        written.append(posixpath.join(base_rel, name))
    for name, data in sorted(result.outputs.items()):
        relative = posixpath.normpath(name)
        if relative.startswith(("..", "/")):
            continue
        target = base.joinpath("out", *relative.split("/"))
        if base.resolve() not in target.resolve().parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(posixpath.join(base_rel, "out", relative))
    return tuple(written[:200])


__all__ = ["Evidence", "ExecutionOutcome", "ExecutionOwner", "SandboxService"]
