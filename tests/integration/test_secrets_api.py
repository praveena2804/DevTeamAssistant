"""C.2 integration: API start-up fails closed (D-18) and the D-18a migration end to end (AP-007)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from devteam_api import token_checks
from devteam_api.main import create_app
from devteam_core.config import Settings
from devteam_harness.iam import TokenMigrationRequiredError
from devteam_harness.secrets import SecretsUnavailableError
from tests.conftest import audit_rows
from tests.scripts.test_scripts import load_script

pytestmark = [pytest.mark.integration, pytest.mark.security]

TOKEN = "Mig-" + "0123456789abcdef" * 3  # gitleaks:allow (synthetic)


def _start(settings: Settings) -> TestClient:
    client = TestClient(create_app(settings, configure_logs=False))
    client.__enter__()
    return client


def test_api_refuses_to_start_while_the_plaintext_key_file_exists(isolated_settings: Settings) -> None:
    isolated_settings.data_dir.mkdir(parents=True, exist_ok=True)
    isolated_settings.api_token_path.write_text(TOKEN, encoding="utf-8")
    with pytest.raises(TokenMigrationRequiredError) as info:
        _start(isolated_settings)
    assert TOKEN not in str(info.value)
    assert isolated_settings.api_token_path.read_text(encoding="utf-8") == TOKEN  # untouched


def test_api_refuses_to_start_without_a_secure_keyring(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import keyring  # noqa: PLC0415

    insecure = type("PlaintextKeyring", (), {"__module__": "keyrings.alt.file", "priority": 1})()
    monkeypatch.setattr(keyring, "get_keyring", lambda: insecure)
    keyring_settings = isolated_settings.model_copy(
        update={"secrets": isolated_settings.secrets.model_copy(update={"backend": "keyring"})}
    )
    with pytest.raises(SecretsUnavailableError, match="secure"):
        _start(keyring_settings)
    assert not keyring_settings.api_token_path.exists()  # no plaintext fallback was created


def test_api_starts_with_the_key_from_the_store_and_writes_no_file(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DTTEST_SECRET__API_TOKEN", TOKEN)
    client = _start(isolated_settings)
    try:
        assert client.get("/api/projects", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert client.get("/api/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    finally:
        client.__exit__(None, None, None)
    assert not isolated_settings.api_token_path.exists()


# -------------------------------------------------------------- checks 3 and 4 against the real app


def test_check3_api_authenticates_with_the_stored_key(isolated_settings: Settings) -> None:
    assert token_checks.api_authenticates(isolated_settings)(TOKEN) is True


def test_check4_signin_link_works_and_detects_a_changed_link(isolated_settings: Settings) -> None:
    check = token_checks.signin_link_works(isolated_settings)
    assert check(TOKEN, TOKEN) is True
    assert check(TOKEN, TOKEN[::-1]) is False  # a different stored key would break the existing link


# ------------------------------------------------------------------ migration script end to end


@pytest.fixture
def migrate() -> Any:
    return load_script("migrate_api_token")


def _audit_migration(settings: Settings) -> list[Any]:
    return [row for row in audit_rows(settings) if row.action == "secret.migrated"]


def test_migration_script_success(isolated_settings: Settings, migrate: Any) -> None:
    isolated_settings.data_dir.mkdir(parents=True, exist_ok=True)
    isolated_settings.api_token_path.write_text(TOKEN + "\n", encoding="utf-8")
    result = migrate.run(isolated_settings)
    assert result.status == "migrated" and result.file_deleted
    assert [c.passed for c in result.checks] == [True, True, True, True]
    assert not isolated_settings.api_token_path.exists()
    rows = _audit_migration(isolated_settings)
    assert len(rows) == 1 and rows[0].outcome == "success"
    assert rows[0].principal_id == "service:secrets_migration"
    assert TOKEN not in json.dumps(rows[0].details)
    assert TOKEN not in json.dumps([r.details for r in audit_rows(isolated_settings)])


@pytest.mark.parametrize(
    ("patch", "failed"),
    [
        ("api_authenticates", "api_authenticates"),
        ("signin_link_works", "signin_link_works"),
    ],
)
def test_migration_script_failure_keeps_the_file(
    isolated_settings: Settings, migrate: Any, monkeypatch: pytest.MonkeyPatch, patch: str, failed: str
) -> None:
    isolated_settings.data_dir.mkdir(parents=True, exist_ok=True)
    isolated_settings.api_token_path.write_text(TOKEN, encoding="utf-8")
    refuse: Callable[..., Callable[..., bool]] = lambda _settings: lambda *_: False  # noqa: E731
    monkeypatch.setattr(migrate, patch, refuse)
    result = migrate.run(isolated_settings)
    assert result.status == "failed" and not result.file_deleted
    assert isolated_settings.api_token_path.read_text(encoding="utf-8") == TOKEN
    assert next(c for c in result.checks if not c.passed).name == failed
    rows = _audit_migration(isolated_settings)
    assert len(rows) == 1 and rows[0].outcome == "error" and TOKEN not in json.dumps(rows[0].details)


def test_migration_script_main_reports_without_printing_the_key(
    isolated_settings: Settings,
    migrate: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEVTEAM_ROOT", str(isolated_settings.root))
    monkeypatch.setenv("DEVTEAM_PROFILE", "test")
    isolated_settings.data_dir.mkdir(parents=True, exist_ok=True)
    isolated_settings.api_token_path.write_text(TOKEN, encoding="utf-8")
    assert migrate.main() == 0
    out = capsys.readouterr()
    assert "Migration complete" in out.out and out.out.count("[PASS]") == 4
    assert TOKEN not in out.out + out.err
    assert migrate.main() == 0  # second run: nothing to migrate
    assert "nothing to migrate" in capsys.readouterr().out
