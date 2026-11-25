"""E.4 / AC-E-01: both starter kits build and pass their own tests offline in the sandbox, producing
``TemplateVerification`` records; a broken kit is recorded as failed, never as passed. Real engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.config import Settings
from devteam_core.contracts.sandbox import SandboxExecutionStatus, TemplateVerificationStatus
from devteam_core.contracts.statuses import ModelSlot
from devteam_harness.llm_gateway.providers.ollama import OllamaProvider
from devteam_harness.sandbox.service import SandboxService
from devteam_harness.secrets import SecretsManager
from devteam_services import templates as kit_service
from devteam_services.integration_builder import load_manifest, template_files
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.repositories import sandbox as repo

pytestmark = [pytest.mark.docker, pytest.mark.slow]


@pytest.fixture(scope="module")
def sessions(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sessionmaker[Session]]:
    db = tmp_path_factory.mktemp("state") / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture(scope="module")
def isolated(live_settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Real sandbox settings, but evidence goes to a temporary data folder (copied TLS material)."""
    data = tmp_path_factory.mktemp("data")
    (data / "sandbox_tls").mkdir()
    for item in live_settings.sandbox_tls_dir.iterdir():
        (data / "sandbox_tls" / item.name).write_bytes(item.read_bytes())
    return live_settings.model_copy(
        update={"paths": live_settings.paths.model_copy(update={"data_dir": data})}
    )


@pytest.fixture(scope="module")
def service(
    isolated: Settings, live_secrets: SecretsManager, sessions: sessionmaker[Session], wsl_keepalive: None
) -> Iterator[SandboxService]:
    provider = OllamaProvider(
        isolated.ollama.base_url,
        {ModelSlot.INSTRUCT: "qwen2.5:3b"},  # only used to unload models (Resource Guard)
        timeout_s=60,
    )
    yield SandboxService(isolated, sessions, live_secrets, models=provider)
    provider.close()


@pytest.mark.parametrize("kit", ["html_fastapi_sqlite", "react_fastapi_sqlite"])
def test_starter_kit_self_test_passes_offline(
    kit: str, service: SandboxService, isolated: Settings, sessions: sessionmaker[Session]
) -> None:
    queued = kit_service.request_verification(sessions, kit, requested_by="prn_test")
    with sessions.begin() as session:
        claimed = repo.claim_next_verification(session)
    assert claimed is not None and claimed.verification_id == queued.verification_id
    result = kit_service.run_verification(claimed, service, isolated, sessions)
    detail = [(o.recipe, o.status.value, o.reason) for o in result.recipes]
    assert result.status is TemplateVerificationStatus.PASSED, detail
    assert [o.recipe for o in result.recipes] == list(load_manifest(kit).self_test)
    assert all(o.status is SandboxExecutionStatus.COMPLETED for o in result.recipes)
    tests_run = {o.recipe: o.tests.passed for o in result.recipes if o.tests is not None}
    assert tests_run["pytest_api"] == 3 and tests_run["pytest_db"] == 2
    if kit == "react_fastapi_sqlite":
        assert tests_run["vitest_run"] == 3
    assert result.peak_memory_mb is not None and result.duration_ms is not None
    with sessions() as session:
        stored = repo.get_verification(session, queued.verification_id)
        assert stored is not None and stored.status is TemplateVerificationStatus.PASSED
        assert len(repo.executions_for_verification(session, queued.verification_id)) == len(result.recipes)
    evidence = kit_service.evidence_dir(isolated, queued.verification_id)
    assert any(evidence.rglob("junit.xml"))
    print(
        f"\n{kit}: {result.duration_ms / 1000:.1f}s, peak {result.peak_memory_mb} MB, "
        + ", ".join(f"{o.recipe} {o.duration_ms / 1000:.1f}s/{o.peak_memory_mb}MB" for o in result.recipes)
    )


def test_a_broken_kit_is_recorded_as_failed(
    service: SandboxService,
    isolated: Settings,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(template_id: str, **kw: Any) -> dict[str, bytes]:
        files = template_files(template_id, **kw)
        files["tests/backend/test_health.py"] = files["tests/backend/test_health.py"].replace(
            b'== {"status": "ok"}', b'== {"status": "broken"}'
        )
        return files

    monkeypatch.setattr(kit_service, "template_files", broken)
    queued = kit_service.request_verification(sessions, "html_fastapi_sqlite", requested_by="prn_test")
    with sessions.begin() as session:
        claimed = repo.claim_next_verification(session)
    assert claimed is not None and claimed.verification_id == queued.verification_id
    result = kit_service.run_verification(claimed, service, isolated, sessions)
    assert result.status is TemplateVerificationStatus.FAILED
    assert "pytest_api" in (result.reason or "")
    failed = next(o for o in result.recipes if o.recipe == "pytest_api")
    assert failed.tests is not None and failed.tests.failed == 1


def test_stale_when_the_kit_changes(
    isolated: Settings, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def changed(template_id: str, **kw: Any) -> dict[str, bytes]:
        return {**template_files(template_id, **kw), "frontend/new.js": b"// changed"}

    monkeypatch.setattr(kit_service, "template_files", changed)
    marked = kit_service.refresh_stale(isolated, sessions)
    assert marked >= 1
    with sessions() as session:
        latest = repo.latest_verifications(session)
    assert latest["html_fastapi_sqlite"].status is TemplateVerificationStatus.STALE
