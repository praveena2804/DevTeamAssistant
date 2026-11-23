"""Spike S-1, kept as permanent tests (Part E plan §3.11; D-50). Real engine, real Credential Manager.

1 engine reachable on the Windows loopback endpoint and it is the WSL engine (not Docker Desktop);
2 mutual TLS works, with negative controls (no client certificate, foreign client certificate,
  untrusted server);
3 the encrypted key loads through the intended SDK integration; a wrong passphrase fails;
4 the passphrase comes only from Credential Manager and never appears in config, environment or logs;
5 no decrypted key is written to disk while connecting;
6 certificate and host-name verification stay enabled.
"""

from __future__ import annotations

import os
import socket
import ssl
import tempfile
import time
from pathlib import Path

import pytest
import requests

from devteam_core.config import Settings
from devteam_core.logging.redaction import redact_text, secret_registry
from devteam_harness.sandbox.engine import Engine, PinnedTLSAdapter, build_client, connect, probe
from devteam_harness.sandbox.tls import (
    PASSPHRASE_REF,
    PLAINTEXT_KEY_MARKERS,
    SandboxTLSError,
    build_client_context,
    client_paths,
)
from devteam_harness.secrets import SecretsManager
from devteam_harness.secrets.backends import MemoryBackend

pytestmark = [pytest.mark.docker, pytest.mark.security]

REPO = Path(__file__).resolve().parents[2]


def _ping_with(context: ssl.SSLContext, port: int) -> None:
    """Raw HTTPS GET /_ping with a hand-made context (bypasses our adapter on purpose)."""
    with (
        socket.create_connection(("127.0.0.1", port), timeout=10) as raw,
        context.wrap_socket(raw, server_hostname="127.0.0.1") as tls,
    ):
        tls.sendall(b"GET /_ping HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        reply = tls.recv(64)
    if not reply.startswith(b"HTTP/1.1 200"):
        raise ssl.SSLError(f"engine refused the request: {reply[:20]!r}")


def _port(settings: Settings) -> int:
    return int(settings.sandbox.docker_host.rsplit(":", 1)[1])


# -- 1 + 2: reachable, it is the WSL engine, mutual TLS works --------------------------------------


def test_engine_reachable_over_mutual_tls_and_is_the_wsl_engine(engine: Engine) -> None:
    assert engine.client.ping() is True
    assert engine.info.os == "linux"
    assert "Ubuntu" in engine.info.operating_system
    assert "desktop" not in engine.info.operating_system.lower()
    assert "ubuntu" in engine.client.api.version(api_version=False)["GitCommit"]  # Ubuntu docker.io build


def test_without_client_certificate_the_engine_refuses(live_settings: Settings, wsl_keepalive: None) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(client_paths(live_settings.sandbox_tls_dir)["ca"]))
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        _ping_with(context, _port(live_settings))


def test_with_our_certificate_the_same_raw_request_succeeds(
    live_settings: Settings, live_secrets: SecretsManager, wsl_keepalive: None
) -> None:
    """Negative-control partner: the raw request used above works when the certificate is presented."""
    _ping_with(build_client_context(live_settings.sandbox_tls_dir, live_secrets), _port(live_settings))


def test_client_certificate_from_a_foreign_ca_is_refused(
    live_settings: Settings, rogue_tls: dict[str, Path | str], wsl_keepalive: None
) -> None:
    rogue = Path(rogue_tls["dir"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(client_paths(live_settings.sandbox_tls_dir)["ca"]))
    context.load_cert_chain(
        str(rogue / "cert.pem"), str(rogue / "key.pem"), password=str(rogue_tls["passphrase"])
    )
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        _ping_with(context, _port(live_settings))


def test_server_not_signed_by_our_ca_is_refused_by_the_client(
    live_settings: Settings,
    live_secrets: SecretsManager,
    rogue_tls: dict[str, Path | str],
    wsl_keepalive: None,
) -> None:
    rogue = Path(rogue_tls["dir"])
    paths = client_paths(live_settings.sandbox_tls_dir)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(rogue / "ca.pem"))  # trusts only the rogue CA
    passphrase = live_secrets.require(PASSPHRASE_REF)
    context.load_cert_chain(str(paths["cert"]), str(paths["key"]), password=passphrase.reveal())
    with pytest.raises(ssl.SSLCertVerificationError):
        _ping_with(context, _port(live_settings))
    client, _ = build_client(context, live_settings.sandbox.docker_host)
    with pytest.raises(SandboxTLSError):
        probe(client)


# -- 3: encrypted key through the SDK integration ---------------------------------------------------


def test_client_key_on_disk_is_encrypted_and_loads_through_the_sdk_adapter(
    live_settings: Settings, engine: Engine
) -> None:
    key = client_paths(live_settings.sandbox_tls_dir)["key"].read_bytes()
    assert b"BEGIN ENCRYPTED PRIVATE KEY" in key
    assert not any(marker in key for marker in PLAINTEXT_KEY_MARKERS)
    adapter = engine.client.api.get_adapter("https://127.0.0.1:2376/_ping")
    assert isinstance(adapter, PinnedTLSAdapter)
    assert engine.client.api.verify is True


def test_wrong_passphrase_fails_closed(live_settings: Settings) -> None:
    wrong = SecretsManager(
        MemoryBackend({"platform/sandbox.docker_client_key": "not-the-right-passphrase-123"})
    )
    with pytest.raises(SandboxTLSError):
        build_client_context(live_settings.sandbox_tls_dir, wrong)


def test_missing_passphrase_fails_closed(live_settings: Settings) -> None:
    with pytest.raises(SandboxTLSError):
        build_client_context(live_settings.sandbox_tls_dir, SecretsManager(MemoryBackend({})))


# -- 4: passphrase only in Credential Manager --------------------------------------------------------


def test_passphrase_lives_only_in_the_credential_store(
    live_settings: Settings, live_secrets: SecretsManager, engine: Engine
) -> None:
    # Every check reduces to a boolean or a path: a failure must never print the secret itself.
    value = live_secrets.require(PASSPHRASE_REF).reveal()
    long_enough = len(value) >= 32
    assert long_enough
    in_environment = any(value in env_value for env_value in os.environ.values())
    assert not in_environment
    candidates = [*(REPO / "config").rglob("*.yaml"), REPO / ".env", *live_settings.sandbox_tls_dir.iterdir()]
    if live_settings.logs_dir.is_dir():
        candidates += list(live_settings.logs_dir.glob("*.log*"))
    leaking = [str(path) for path in candidates if path.is_file() and value.encode() in path.read_bytes()]
    assert leaking == []
    registered = value in secret_registry.values()
    assert registered
    redacted_ok = value not in redact_text("x " + value + " x", secret_registry.values())
    assert redacted_ok
    del value


# -- 5: decrypted key never written ------------------------------------------------------------------


def _recent_files(roots: list[Path], since: float, depth: int = 4) -> list[Path]:
    found: list[Path] = []

    def walk(directory: Path, level: int) -> None:
        if level > depth:
            return
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), level + 1)
                elif entry.is_file(follow_symlinks=False) and entry.stat().st_mtime >= since:
                    found.append(Path(entry.path))
            except OSError:
                continue

    for root in roots:
        walk(root, 0)
    return found


def test_no_decrypted_key_is_written_while_connecting(
    live_settings: Settings, live_secrets: SecretsManager, wsl_keepalive: None
) -> None:
    since = time.time() - 1
    with connect(live_settings, live_secrets) as eng:
        assert eng.client.ping()
        eng.client.api.info()
    roots = [live_settings.data_dir, Path(tempfile.gettempdir()), REPO]
    for path in _recent_files(roots, since):
        try:
            head = path.read_bytes()[:1_048_576]
        except OSError:
            continue
        assert not any(marker in head for marker in PLAINTEXT_KEY_MARKERS), path
    for path in live_settings.sandbox_tls_dir.iterdir():
        assert not any(marker in path.read_bytes() for marker in PLAINTEXT_KEY_MARKERS), path


# -- 6: verification stays on --------------------------------------------------------------------------


def test_verification_flags_hold_after_real_requests(engine: Engine) -> None:
    engine.client.api.info()
    context = engine.adapter.context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.cert_store_stats()["x509_ca"] == 1  # requests' CA bundle was never added


def test_weakened_context_is_refused(live_settings: Settings, live_secrets: SecretsManager) -> None:
    context = build_client_context(live_settings.sandbox_tls_dir, live_secrets)
    context.check_hostname = False
    with pytest.raises(SandboxTLSError):
        PinnedTLSAdapter(context)


def test_adapter_refuses_verify_false_and_proxies(engine: Engine) -> None:
    with pytest.raises(SandboxTLSError):
        engine.client.api.get("https://127.0.0.1:2376/_ping", verify=False)
    with pytest.raises(SandboxTLSError):
        engine.adapter.proxy_manager_for("http://proxy.invalid:3128")
    assert engine.client.api.trust_env is False


def test_adapter_refuses_other_hosts(engine: Engine) -> None:
    with pytest.raises(SandboxTLSError):
        engine.client.api.get("https://localhost:2376/_ping")
    with pytest.raises((SandboxTLSError, requests.RequestException)):
        engine.client.api.get("https://198.51.100.1:2376/_ping", timeout=2)
