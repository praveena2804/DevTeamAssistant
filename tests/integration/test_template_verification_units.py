"""Starter-kit verification outcomes without Docker (the real engine proves the passing path in
``tests/sandbox/test_template_verification.py``). A fake execution service returns each kind of result, so
every branch of ``run_verification`` is checked: passed, failed, blocked, and resources that never free up.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.config import Settings, load_settings
from devteam_core.contracts.sandbox import (
    ContainerLimits,
    SandboxExecution,
    SandboxExecutionStatus,
    TemplateVerificationStatus,
    TestCounts,
)
from devteam_core.ids import new_id
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.sandbox.recipes import get_recipe
from devteam_harness.sandbox.service import ExecutionOutcome
from devteam_services import templates as kit_service
from devteam_services.integration_builder import load_manifest
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.repositories import sandbox as repo
from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 24, tzinfo=UTC)
LIMITS = ContainerLimits(
    memory_mb=1536, cpus=1.5, pids=256, wall_clock_s=600, tmpfs_mb=256, work_copy_mb=512, output_kb=1024
)
S = SandboxExecutionStatus


class FakeSandbox:
    """``mode``: pass | fail (pytest_api has a failing test) | blocked | busy (resources never free)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[str] = []

    def execute(self, *, recipe: str, owner: Any, **_kw: Any) -> ExecutionOutcome:
        self.calls.append(recipe)
        if self.mode == "busy":
            raise ResourceUnavailableError("resource.low_memory", "100 MB free, a sandbox run needs 2048 MB")
        base: dict[str, Any] = {
            "execution_id": new_id("sbx"), "verification_id": owner.verification_id, "recipe": recipe,
            "image": "devteam-sandbox/python_api", "limits": LIMITS, "started_at": NOW, "finished_at": NOW,
        }  # fmt: skip
        if self.mode == "blocked":
            execution = SandboxExecution(
                **base, status=S.BLOCKED, duration_ms=0, reason="sandbox.docker_unavailable"
            )
        elif self.mode == "fail" and recipe == "pytest_api":
            tests = TestCounts(tests=3, passed=2, failed=1, errors=0, skipped=0)
            execution = SandboxExecution(
                **base, status=S.FAILED, exit_code=1, duration_ms=10, tests=tests, reason="tests failed"
            )
        else:
            tests = TestCounts(tests=2, passed=2, failed=0, errors=0, skipped=0)
            execution = SandboxExecution(
                **base, status=S.COMPLETED, exit_code=0, duration_ms=10, peak_memory_mb=50.0,
                tests=tests if get_recipe(recipe).reports_tests else None,
            )  # fmt: skip
        return ExecutionOutcome(execution, None, None, {})


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    real = load_settings(root=REPO_ROOT, profile="test", environ={})
    return real.model_copy(update={"paths": real.paths.model_copy(update={"data_dir": tmp_path / "data"})})


def _run(mode: str, settings: Settings, sessions: sessionmaker[Session]) -> tuple[Any, FakeSandbox]:
    queued = kit_service.request_verification(sessions, "html_fastapi_sqlite", requested_by="prn_test")
    with sessions.begin() as session:
        claimed = repo.claim_next_verification(session)
    assert claimed is not None and claimed.verification_id == queued.verification_id
    fake = FakeSandbox(mode)
    pauses: list[float] = []
    result = kit_service.run_verification(
        claimed,
        fake,  # type: ignore[arg-type]
        settings,
        sessions,
        sleep=pauses.append,
        resource_attempts=3,
    )
    if mode == "busy":
        assert len(pauses) == 3  # bounded: it waits, then gives up honestly
    with sessions() as session:
        stored = repo.get_verification(session, queued.verification_id)
    assert stored is not None and stored.status is result.status  # what is returned is what is stored
    return result, fake


def test_every_recipe_successful_is_passed(settings: Settings, sessions: sessionmaker[Session]) -> None:
    result, fake = _run("pass", settings, sessions)
    assert result.status is TemplateVerificationStatus.PASSED and result.reason is None
    assert fake.calls == list(load_manifest("html_fastapi_sqlite").self_test)
    assert result.peak_memory_mb == 50.0 and result.template_checksum and result.image_digests


def test_a_failing_test_makes_it_failed_and_names_the_recipe(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    result, _ = _run("fail", settings, sessions)
    assert result.status is TemplateVerificationStatus.FAILED
    assert result.reason == "recipes not successful: pytest_api"


def test_a_safety_block_stops_at_the_first_recipe(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    result, fake = _run("blocked", settings, sessions)
    assert result.status is TemplateVerificationStatus.BLOCKED and len(fake.calls) == 1
    assert result.reason == "sandbox.docker_unavailable"


def test_resources_that_never_free_up_end_blocked_and_nothing_ran(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    result, _ = _run("busy", settings, sessions)
    assert result.status is TemplateVerificationStatus.BLOCKED and result.recipes == ()
    assert result.reason is not None and result.reason.startswith("resource.low_memory")
    assert "still unavailable after 3 tries" in result.reason


def test_requests_are_refused_for_unknown_kits_and_duplicates(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(kit_service.VerificationRefused, match="unknown starter kit"):
        kit_service.request_verification(sessions, "angular_dotnet", requested_by="prn_test")
    kit_service.request_verification(sessions, "html_fastapi_sqlite", requested_by="prn_test")
    with pytest.raises(kit_service.VerificationRefused, match="already queued"):
        kit_service.request_verification(sessions, "html_fastapi_sqlite", requested_by="prn_test")


def test_stale_check_without_an_image_lock_marks_nothing(
    settings: Settings, sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    missing = settings.model_copy(
        update={"sandbox": settings.sandbox.model_copy(update={"image_lock_path": tmp_path / "none.json"})}
    )
    assert kit_service.refresh_stale(missing, sessions) == 0
    assert (
        kit_service.evidence_dir(settings, "tvf_x")
        == settings.data_dir / "sandbox" / "verifications" / "tvf_x"
    )
