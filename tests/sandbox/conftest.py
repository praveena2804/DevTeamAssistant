"""Fixtures for tests that talk to the real Docker Engine in WSL2 (marker ``docker``).

These tests never mock the engine. If the engine is not reachable they **fail** (they are only selected
when a Docker run is intended), so an unexecuted check can never look like a pass.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from devteam_core.config import Settings, load_settings
from devteam_harness.sandbox.engine import Engine, connect
from devteam_harness.secrets import SecretsManager, secrets_manager_from_settings


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    """The real configuration (default profile): real TLS files and the real Credential Manager entry."""
    return load_settings()


@pytest.fixture(scope="session")
def live_secrets(live_settings: Settings) -> SecretsManager:
    return secrets_manager_from_settings(live_settings)


@pytest.fixture(scope="session")
def wsl_keepalive(live_settings: Settings) -> Iterator[None]:
    """WSL stops an idle distribution (and the engine with it); keep one idle session open (plan §3.10)."""
    proc = subprocess.Popen(
        ["wsl.exe", "-d", live_settings.sandbox.wsl_distribution, "--exec", "sleep", "infinity"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def engine(live_settings: Settings, live_secrets: SecretsManager, wsl_keepalive: None) -> Iterator[Engine]:
    deadline = time.monotonic() + 60
    while True:
        try:
            eng = connect(live_settings, live_secrets)
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)
    try:
        yield eng
    finally:
        eng.close()


def wsl_root(settings: Settings, command: str, stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["wsl.exe", "-d", settings.sandbox.wsl_distribution, "-u", "root", "--", "sh", "-c", command],
        input=stdin,
        capture_output=True,
        check=True,
        timeout=300,
    )
    return proc.stdout


@pytest.fixture(scope="session")
def rogue_tls(
    live_settings: Settings, tmp_path_factory: pytest.TempPathFactory, wsl_keepalive: None
) -> dict[str, Path | str]:
    """A second, unrelated CA with its own client certificate (key encrypted too), for negative controls."""
    out = tmp_path_factory.mktemp("rogue_tls")
    passphrase = "rogue-test-passphrase-0123456789"
    script = """set -eu
umask 077
W=$(mktemp -d)
cd "$W"
IFS= read -r P
openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout ca-key.pem \
  -out ca.pem -days 2 -subj "/CN=rogue CA" -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign" 2>/dev/null
printf '%s' "$P" | openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 \
  -aes-256-cbc -pass stdin -out key.pem
printf '%s' "$P" | openssl req -new -key key.pem -passin stdin -out c.csr -subj "/CN=rogue"
printf '%s\\n' "extendedKeyUsage=clientAuth" > c.ext
openssl x509 -req -in c.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 2 -extfile c.ext \
  -out cert.pem 2>/dev/null
tar -cf - ca.pem cert.pem key.pem
cd /; rm -rf "$W"
"""
    wsl_root(live_settings, "umask 077; cat > /root/devteam-rogue.sh", stdin=script.encode())
    try:
        blob = wsl_root(live_settings, "sh /root/devteam-rogue.sh", stdin=(passphrase + "\n").encode())
    finally:
        wsl_root(live_settings, "rm -f /root/devteam-rogue.sh")
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member)
            assert data is not None
            (out / member.name).write_bytes(data.read())
    return {"dir": out, "passphrase": passphrase}
