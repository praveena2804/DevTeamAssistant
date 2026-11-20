"""C.2 secrets (HC-17, HS-09; D-18, D-18a): keyring only, no plaintext fallback, never leaked.

Written before the implementation (test-first). The canary value below is synthetic.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from devteam_core.config import Settings
from devteam_core.errors import ConfigError
from devteam_core.logging import secret_registry
from devteam_harness.iam import API_TOKEN_REF, TokenMigrationRequiredError, load_api_token
from devteam_harness.secrets import (
    KeyringBackend,
    MemoryBackend,
    SecretsManager,
    SecretsUnavailableError,
    SecretValue,
    migrate_legacy_token,
    secrets_manager_from_settings,
)

pytestmark = pytest.mark.security

CANARY = "canary-SECRET-7f3e9a1c0b5d4e2f8a6b9c0d1e2f3a4b5c6d7e8f"  # gitleaks:allow (synthetic)
TOKEN = "Tk9-" + "abcdefghij" * 4  # not a palindrome (the read-back fault reverses values)


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    before = secret_registry.values()
    yield
    secret_registry.clear()
    for value in before:
        secret_registry.register(value)


def manager() -> SecretsManager:
    return SecretsManager(MemoryBackend())


# ---------------------------------------------------------------------------------- basic behaviour


def test_put_get_delete_round_trip() -> None:
    secrets = manager()
    assert secrets.get("secret://platform/demo") is None
    secrets.put("secret://platform/demo", CANARY)
    value = secrets.require("secret://platform/demo")
    assert value.reveal() == CANARY and value.matches(CANARY) and not value.matches(CANARY + "x")
    assert secrets.exists("secret://platform/demo")
    secrets.delete("secret://platform/demo")
    assert not secrets.exists("secret://platform/demo")


@pytest.mark.parametrize(
    "ref",
    [
        "platform/demo",
        "secret://platform/../x",
        "secret://other/x",
        "secret://platform/",
        "file:///tmp/x",
        "",
    ],
)
def test_invalid_references_rejected(ref: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        manager().get(ref)


def test_short_secrets_rejected() -> None:
    with pytest.raises(ValueError):
        manager().put("secret://platform/demo", "abc")


def test_values_are_registered_for_redaction_on_put_and_get() -> None:
    backend = MemoryBackend({"platform/demo": CANARY})
    assert CANARY not in secret_registry.values()
    SecretsManager(backend).require("secret://platform/demo")
    assert CANARY in secret_registry.values()


# --------------------------------------------------------------------------------------- leakage


def test_secret_value_never_prints() -> None:
    value = SecretValue(CANARY)
    for rendering in (str(value), repr(value), f"{value}", json.dumps({"v": str(value)})):
        assert CANARY not in rendering


def test_canary_never_reaches_logs_exceptions_or_audit(
    caplog: pytest.LogCaptureFixture, isolated_settings: Settings
) -> None:
    from devteam_core.logging import configure_logging, get_logger, redact_value  # noqa: PLC0415

    secrets = manager()
    secrets.put("secret://platform/canary", CANARY)
    value = secrets.require("secret://platform/canary")

    # logs: configured exactly like the app; checked on our handler, a foreign handler and pytest capture
    ours = io.StringIO()
    configure_logging(isolated_settings, stream=ours)
    foreign = io.StringIO()
    handler = logging.StreamHandler(foreign)  # e.g. a third-party library's handler, no redaction of its own
    logging.getLogger().addHandler(handler)
    try:
        get_logger("test").warning("leak_attempt", value=value.reveal(), nested={"x": [value.reveal()]})
        logging.getLogger("thirdparty").warning("stdlib record with %s", value.reveal())
    finally:
        logging.getLogger().removeHandler(handler)
    assert (
        "leak_attempt" in ours.getvalue() and "leak_attempt" in foreign.getvalue()
    )  # the lines were emitted
    assert CANARY not in ours.getvalue()
    assert CANARY not in foreign.getvalue().split("stdlib record")[0]  # structlog events: redacted at source
    assert CANARY not in caplog.text.split("stdlib record")[0]
    assert CANARY not in ours.getvalue().split("stdlib record", 1)[-1]  # foreign stdlib record: our formatter

    # exceptions raised by the manager name the reference, never the value
    with pytest.raises(ValueError) as info:
        secrets.put("secret://platform/canary2", "x" * 3 + "\n")
    assert CANARY not in str(info.value)

    # audit details are redacted before storage (devteam_state audit repository uses redact_value)
    assert CANARY not in json.dumps(redact_value({"note": f"value was {value.reveal()}"}))


def test_backend_errors_do_not_echo_values() -> None:
    class Exploding(MemoryBackend):
        def set(self, key: str, value: str) -> None:
            raise RuntimeError(f"backend failed while storing {value}")

    with pytest.raises(SecretsUnavailableError) as info:
        SecretsManager(Exploding()).put("secret://platform/x", CANARY)
    assert CANARY not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


# ------------------------------------------------------------- keyring backend: fail closed (D-18)


def _fake_backend(module: str) -> object:
    """An object that looks like a keyring backend defined in ``module``."""
    cls = type("FakeKeyring", (), {"__module__": module, "priority": 1, "get_password": lambda *_: None})
    return cls()


@pytest.mark.parametrize(
    "module",
    [
        "keyrings.alt.file",  # plaintext / encrypted file keyrings
        "keyring.backends.fail",
        "keyring.backends.null",
        "keyring.backends.chainer",
        "some.attacker.module",
    ],
)
def test_insecure_or_unknown_keyring_backends_refused(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    import keyring  # noqa: PLC0415

    monkeypatch.setattr(keyring, "get_keyring", lambda: _fake_backend(module))
    with pytest.raises(SecretsUnavailableError, match="secure"):
        KeyringBackend("devteam-ai-test")


def test_windows_credential_manager_backend_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring  # noqa: PLC0415

    monkeypatch.setattr(keyring, "get_keyring", lambda: _fake_backend("keyring.backends.Windows"))
    assert KeyringBackend("devteam-ai-test")


def test_memory_backend_is_refused_outside_tests(isolated_settings: Settings) -> None:
    production = isolated_settings.model_copy(update={"profile": "low_memory"})
    with pytest.raises(ConfigError, match="test profile"):
        secrets_manager_from_settings(production)


def test_settings_reject_memory_backend_outside_tests() -> None:
    from devteam_core.config import load_settings  # noqa: PLC0415

    with pytest.raises(ConfigError, match="test profile"):
        load_settings(profile="low_memory", environ={"DEVTEAM_SECRETS__BACKEND": "memory"})


def test_no_plaintext_file_is_ever_created(isolated_settings: Settings) -> None:
    secrets = secrets_manager_from_settings(isolated_settings)
    token = load_api_token(secrets, isolated_settings.api_token_path)
    assert len(token) >= 40
    assert not isolated_settings.api_token_path.exists()
    leftovers = [
        p
        for p in isolated_settings.data_dir.rglob("*")
        if p.is_file() and token in p.read_text("utf-8", "ignore")
    ]
    assert leftovers == []


def test_test_seed_environment_is_only_read_in_the_test_profile(isolated_settings: Settings) -> None:
    secrets = secrets_manager_from_settings(isolated_settings, environ={"DTTEST_SECRET__API_TOKEN": TOKEN})
    assert secrets.require(API_TOKEN_REF).reveal() == TOKEN


# ------------------------------------------------------------------------------ API token (HC-15)


def test_api_token_is_created_in_the_keyring_once(tmp_path: Path) -> None:
    secrets = manager()
    first = load_api_token(secrets, tmp_path / "api_token")
    assert load_api_token(secrets, tmp_path / "api_token") == first
    assert first in secret_registry.values()


def test_api_refuses_to_start_while_a_plaintext_key_file_exists(tmp_path: Path) -> None:
    legacy = tmp_path / "api_token"
    legacy.write_text(TOKEN, encoding="utf-8")
    with pytest.raises(TokenMigrationRequiredError) as info:
        load_api_token(manager(), legacy)
    assert TOKEN not in str(info.value) and "migrate_api_token" in str(info.value)


def test_short_stored_token_refused(tmp_path: Path) -> None:
    secrets = SecretsManager(MemoryBackend({"platform/api_token": "short-token"}))
    with pytest.raises(SecretsUnavailableError, match="too short"):
        load_api_token(secrets, tmp_path / "api_token")


# --------------------------------------------------------------- D-18a migration, the four checks


def _legacy(tmp_path: Path, value: str = TOKEN) -> Path:
    path = tmp_path / "api_token"
    path.write_text(value + "\n", encoding="utf-8")
    return path


def _ok(_: str) -> bool:
    return True


def _ok2(_: str, __: str) -> bool:
    return True


def test_successful_migration_runs_all_checks_in_order_then_deletes(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    calls: list[str] = []
    secrets = manager()

    def api(token: str) -> bool:
        calls.append("api")
        return token == TOKEN

    def link(original: str, stored: str) -> bool:
        calls.append("link")
        assert legacy.exists(), "the file must still exist while checks run"
        return original == stored == TOKEN

    result = migrate_legacy_token(secrets, legacy, ref=API_TOKEN_REF, api_accepts=api, signin_link_works=link)
    assert result.status == "migrated" and result.file_deleted
    assert [c.name for c in result.checks] == [
        "stored",
        "read_back_matches",
        "api_authenticates",
        "signin_link_works",
    ]
    assert all(c.passed for c in result.checks)
    assert calls == ["api", "link"]
    assert not legacy.exists()
    assert secrets.require(API_TOKEN_REF).reveal() == TOKEN
    assert TOKEN not in json.dumps(result.as_record())


class _FailingSet(MemoryBackend):
    def set(self, key: str, value: str) -> None:
        raise OSError("credential manager locked")


class _WrongReadBack(MemoryBackend):
    def get(self, key: str) -> str | None:
        value = super().get(key)
        return None if value is None else value[::-1]


@pytest.mark.parametrize(
    ("backend", "api", "link", "failed_check"),
    [
        (_FailingSet(), _ok, _ok2, "stored"),
        (_WrongReadBack(), _ok, _ok2, "read_back_matches"),
        (MemoryBackend(), lambda _: False, _ok2, "api_authenticates"),
        (MemoryBackend(), _ok, lambda _a, _b: False, "signin_link_works"),
        (MemoryBackend(), lambda _: (_ for _ in ()).throw(RuntimeError(TOKEN)), _ok2, "api_authenticates"),
    ],
)
def test_any_failed_check_stops_and_keeps_the_file(
    tmp_path: Path,
    backend: MemoryBackend,
    api: Callable[[str], bool],
    link: Callable[[str, str], bool],
    failed_check: str,
) -> None:
    legacy = _legacy(tmp_path)
    result = migrate_legacy_token(
        SecretsManager(backend), legacy, ref=API_TOKEN_REF, api_accepts=api, signin_link_works=link
    )
    assert result.status == "failed" and not result.file_deleted
    assert legacy.read_text(encoding="utf-8").strip() == TOKEN  # original key preserved, unchanged
    names = [c.name for c in result.checks]
    failed_at = names.index(failed_check)
    assert all(c.passed for c in result.checks[:failed_at])
    assert not result.checks[failed_at].passed
    assert all(c.detail == "not run" for c in result.checks[failed_at + 1 :])
    assert TOKEN not in json.dumps(result.as_record())


def test_migration_never_overwrites_a_different_stored_key(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    secrets = SecretsManager(MemoryBackend({"platform/api_token": "D" * 43}))
    result = migrate_legacy_token(secrets, legacy, ref=API_TOKEN_REF, api_accepts=_ok, signin_link_works=_ok2)
    assert result.status == "failed" and legacy.exists()
    assert secrets.require(API_TOKEN_REF).reveal() == "D" * 43


def test_rerun_after_interruption_completes(tmp_path: Path) -> None:
    """Key already stored (same value) but the file still exists: checks run again, then delete."""
    legacy = _legacy(tmp_path)
    secrets = SecretsManager(MemoryBackend({"platform/api_token": TOKEN}))
    result = migrate_legacy_token(secrets, legacy, ref=API_TOKEN_REF, api_accepts=_ok, signin_link_works=_ok2)
    assert result.status == "migrated" and not legacy.exists()


def test_nothing_to_migrate(tmp_path: Path) -> None:
    result = migrate_legacy_token(
        manager(), tmp_path / "api_token", ref=API_TOKEN_REF, api_accepts=_ok, signin_link_works=_ok2
    )
    assert result.status == "nothing_to_migrate" and result.checks == ()


def test_short_legacy_file_is_not_migrated(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path, "short")
    result = migrate_legacy_token(
        manager(), legacy, ref=API_TOKEN_REF, api_accepts=_ok, signin_link_works=_ok2
    )
    assert result.status == "failed" and legacy.exists()


# ------------------------------------------------------------- real Windows Credential Manager


@pytest.mark.keyring
def test_real_credential_manager_round_trip() -> None:
    """Local only: touches the real OS keyring under a throw-away service name, then removes it."""
    backend = KeyringBackend("devteam-ai-selftest")
    secrets = SecretsManager(backend)
    ref = "secret://platform/selftest"
    try:
        secrets.put(ref, CANARY)
        assert secrets.require(ref).matches(CANARY)
    finally:
        secrets.delete(ref)
    assert not secrets.exists(ref)


def test_keyring_backend_errors_are_wrapped_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring  # noqa: PLC0415

    class PasswordDeleteError(Exception):
        pass

    def boom(*args: Any) -> None:
        raise RuntimeError(f"failed with {args}")

    def absent(*_: Any) -> None:
        raise PasswordDeleteError("not found")

    broken = type(
        "WinVaultKeyring",
        (),
        {
            "__module__": "keyring.backends.Windows",
            "get_password": boom,
            "set_password": boom,
            "delete_password": boom,
        },
    )()
    monkeypatch.setattr(keyring, "get_keyring", lambda: broken)
    backend = KeyringBackend("devteam-ai-test")
    assert backend.description == "keyring.backends.Windows.WinVaultKeyring"
    for call in (
        lambda: backend.get("platform/x"),
        lambda: backend.set("platform/x", CANARY),
        lambda: backend.delete("platform/x"),
    ):
        with pytest.raises(SecretsUnavailableError) as info:
            call()
        assert CANARY not in str(info.value) and info.value.__suppress_context__

    already_gone = type(
        "WinVaultKeyring",
        (),
        {"__module__": "keyring.backends.Windows", "get_password": lambda *_: 42, "delete_password": absent},
    )()
    monkeypatch.setattr(keyring, "get_keyring", lambda: already_gone)
    backend = KeyringBackend("devteam-ai-test")
    backend.delete("platform/x")  # deleting an absent secret is not an error
    assert backend.get("platform/x") is None  # a non-string value is never returned as a secret
