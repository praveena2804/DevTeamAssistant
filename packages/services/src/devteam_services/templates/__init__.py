"""Starter-kit self-test → ``TemplateVerification`` records (Part E plan §3.6; D-49).

``request_verification`` (API or script) only queues a request; the **worker** claims it and calls
:func:`run_verification`, which runs the kit's ``self_test`` recipes one by one in the sandbox, offline.
The record is ``passed`` only if every recipe completed with exit code 0 and every test recipe executed at
least one test with no failures or errors (enforced by the ``TemplateVerification`` contract). A safety
precondition that fails (engine down, image changed) makes it ``blocked``; nothing runs elsewhere.
A changed kit or image marks earlier results ``stale``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.config import Settings
from devteam_core.contracts.sandbox import (
    RecipeOutcome,
    SandboxExecutionStatus,
    TemplateVerification,
    TemplateVerificationStatus,
)
from devteam_core.ids import new_id
from devteam_harness import audit
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.iam.principals import ServiceName, ServicePrincipal
from devteam_harness.sandbox.images import ImageLock
from devteam_harness.sandbox.recipes import get_recipe
from devteam_harness.sandbox.service import Evidence, ExecutionOwner, SandboxService
from devteam_services.integration_builder import load_manifest, template_checksum, template_files, template_ids
from devteam_state.repositories import sandbox as repo

PRINCIPAL = ServicePrincipal(ServiceName.INTEGRATION_BUILDER).id


class VerificationRefused(ValueError):
    pass


def request_verification(
    sessions: sessionmaker[Session], template_id: str, *, requested_by: str, clock: Clock = system_clock
) -> TemplateVerification:
    """Queue a self-test (D-49: started by a person). One queued/running request per kit at a time."""
    if template_id not in template_ids():
        raise VerificationRefused(f"unknown starter kit {template_id!r}")
    manifest = load_manifest(template_id)
    queued = TemplateVerification(
        verification_id=new_id("tvf", clock),
        template_id=template_id,
        template_version=manifest.version,
        status=TemplateVerificationStatus.QUEUED,
        requested_by=requested_by,
        requested_at=clock.now(),
    )
    with sessions.begin() as session:
        if repo.has_active_verification(session, template_id):
            raise VerificationRefused("a verification of this starter kit is already queued or running")
        repo.add_verification(session, queued)
        audit.record(
            session, action="template.verification_requested", outcome="success", principal_id=requested_by,
            target_type="template", target_id=template_id, details={"verification_id": queued.verification_id},
        )  # fmt: skip
    return queued


def current_fingerprint(settings: Settings, template_id: str) -> tuple[str, dict[str, str]]:
    """The kit's checksum and the locked image IDs its recipes use."""
    manifest = load_manifest(template_id)
    checksum = template_checksum(template_files(template_id))
    lock = ImageLock.load(settings.sandbox_image_lock_path)
    images = sorted({get_recipe(recipe).image for recipe in manifest.self_test})
    return checksum, {name: lock.get(name).image_id for name in images}


def run_verification(
    verification: TemplateVerification,
    sandbox: SandboxService,
    settings: Settings,
    sessions: sessionmaker[Session],
    *,
    clock: Clock = system_clock,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    resource_attempts: int = 10,
) -> TemplateVerification:
    """Run every self-test recipe and store the final result. Waiting for resources propagates (re-queued)."""
    import time  # noqa: PLC0415

    tick = monotonic or time.monotonic
    started = tick()
    manifest = load_manifest(verification.template_id)
    files = template_files(verification.template_id)
    checksum, digests = current_fingerprint(settings, verification.template_id)
    evidence = Evidence(root=settings.data_dir, prefix=f"sandbox/verifications/{verification.verification_id}")
    owner = ExecutionOwner(verification_id=verification.verification_id)
    outcomes: list[RecipeOutcome] = []
    blocked_reason: str | None = None
    pause = sleep or time.sleep
    for recipe in manifest.self_test:
        outcome = None
        for _attempt in range(resource_attempts):
            try:
                outcome = sandbox.execute(
                    recipe=recipe, args={}, files=files, owner=owner, principal_id=PRINCIPAL, evidence=evidence
                )
                break
            except ResourceUnavailableError as exc:  # waiting for memory or for a model call to finish
                blocked_reason = f"{exc.code}: {exc.message} (still unavailable after {resource_attempts} tries)"
                pause(settings.orchestration.resource_wait_s)
        if outcome is None:
            break  # blocked_reason explains why; nothing ran for this recipe
        blocked_reason = None
        execution = outcome.execution
        outcomes.append(
            RecipeOutcome(
                recipe=recipe,
                status=execution.status,
                execution_id=execution.execution_id,
                exit_code=execution.exit_code,
                tests=execution.tests,
                reports_tests=get_recipe(recipe).reports_tests,
                duration_ms=execution.duration_ms,
                peak_memory_mb=execution.peak_memory_mb,
                reason=execution.reason,
            )
        )
        if execution.status is SandboxExecutionStatus.BLOCKED:
            blocked_reason = execution.reason or "a sandbox safety precondition failed"
            break
    peaks = [o.peak_memory_mb for o in outcomes if o.peak_memory_mb is not None]
    if blocked_reason is not None:
        status, reason = TemplateVerificationStatus.BLOCKED, blocked_reason
    elif all(o.succeeded for o in outcomes):
        status, reason = TemplateVerificationStatus.PASSED, None
    else:
        failed = [o.recipe for o in outcomes if not o.succeeded]
        status, reason = TemplateVerificationStatus.FAILED, f"recipes not successful: {', '.join(failed)}"
    result = verification.model_copy(
        update={
            "status": status,
            "recipes": tuple(outcomes),
            "template_checksum": checksum,
            "image_digests": digests,
            "duration_ms": int((tick() - started) * 1000),
            "peak_memory_mb": max(peaks) if peaks else None,
            "finished_at": clock.now(),
            "reason": reason,
        }
    )
    result = TemplateVerification.model_validate(result.model_dump())  # re-check the honesty rules
    with sessions.begin() as session:
        repo.finish_verification(session, result)
        audit.record(
            session, action="template.verified", outcome="success" if status is TemplateVerificationStatus.PASSED
            else "error", principal_id=PRINCIPAL, target_type="template", target_id=verification.template_id,
            details={"verification_id": result.verification_id, "status": status.value,
                     "duration_ms": result.duration_ms, "peak_memory_mb": result.peak_memory_mb},
        )  # fmt: skip
    return result


def refresh_stale(settings: Settings, sessions: sessionmaker[Session]) -> int:
    """Mark results whose kit or image changed as stale. Returns how many were marked."""
    changed = 0
    for template_id in template_ids():
        try:
            checksum, digests = current_fingerprint(settings, template_id)
        except Exception:  # noqa: BLE001, S112 - no image lock yet: nothing can be compared
            continue
        with sessions.begin() as session:
            changed += repo.mark_stale(session, template_id=template_id, current_checksum=checksum,
                                       current_digests=digests)  # fmt: skip
    return changed


def evidence_dir(settings: Settings, verification_id: str) -> Path:
    return settings.data_dir / "sandbox" / "verifications" / verification_id


__all__ = [
    "PRINCIPAL",
    "ResourceUnavailableError",
    "VerificationRefused",
    "current_fingerprint",
    "evidence_dir",
    "refresh_stale",
    "request_verification",
    "run_verification",
]
