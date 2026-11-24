"""Mutual-TLS client context for the WSL Docker Engine (HC-14; D-50; Part E plan §3.11).

- The CA certificate in ``data/sandbox_tls`` is the **only** trust anchor (no system or certifi roots).
- Certificate verification and host-name checking are always on; nothing can switch them off.
- The client private key exists on disk **only encrypted** (PKCS#8). Its passphrase comes from Windows
  Credential Manager through the platform secret store; OpenSSL decrypts the key in memory inside
  ``load_cert_chain``. The decrypted key is never written anywhere.
"""

from __future__ import annotations

import ssl
from pathlib import Path

from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.secrets import SecretsManager
from devteam_harness.secrets.backends import SecretsUnavailableError

PASSPHRASE_REF = "secret://platform/sandbox.docker_client_key"  # noqa: S105 - a reference, not a value
CLIENT_FILES = {"ca": "ca.pem", "cert": "client-cert.pem", "key": "client-key.enc.pem"}
_DASHES = b"-----"  # markers are assembled so secret scanners do not mistake them for a key
ENCRYPTED_MARKER = _DASHES + b"BEGIN ENCRYPTED PRIVATE KEY" + _DASHES
PLAINTEXT_KEY_MARKERS = tuple(
    _DASHES + b"BEGIN " + kind + b"PRIVATE KEY" + _DASHES for kind in (b"", b"RSA ", b"EC ")
)


class SandboxTLSError(SandboxBlockedError):
    """TLS material is missing or invalid. The sandbox is Blocked; nothing falls back to plain TCP."""

    def __init__(self, message: str) -> None:
        super().__init__("sandbox.tls_not_configured", message)


def client_paths(tls_dir: Path) -> dict[str, Path]:
    return {key: tls_dir / name for key, name in CLIENT_FILES.items()}


def check_key_file(path: Path) -> None:
    """The client key must be stored encrypted; a plaintext key is refused (never used)."""
    data = path.read_bytes()
    if any(marker in data for marker in PLAINTEXT_KEY_MARKERS):
        raise SandboxTLSError("the sandbox client key is stored unencrypted; refusing to use it")
    if ENCRYPTED_MARKER not in data:
        raise SandboxTLSError("the sandbox client key is not an encrypted PKCS#8 key")


def assert_verifying(context: ssl.SSLContext) -> None:
    """Fail closed if certificate or host-name verification has been weakened."""
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise SandboxTLSError("TLS verification is not fully enabled; refusing to connect")
    if context.minimum_version < ssl.TLSVersion.TLSv1_2:
        raise SandboxTLSError("TLS minimum version below 1.2; refusing to connect")


def build_client_context(tls_dir: Path, secrets: SecretsManager) -> ssl.SSLContext:
    paths = client_paths(tls_dir)
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise SandboxTLSError(
            f"sandbox TLS files missing ({', '.join(sorted(missing))}); run scripts/setup_wsl_docker.py"
        )
    check_key_file(paths["key"])
    try:
        passphrase = secrets.require(PASSPHRASE_REF)
    except SecretsUnavailableError:
        raise SandboxTLSError("the sandbox client key passphrase is not in the credential store") from None

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # CERT_REQUIRED + check_hostname by default
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=str(paths["ca"]))  # the private CA is the only trust anchor
    try:
        context.load_cert_chain(
            certfile=str(paths["cert"]),
            keyfile=str(paths["key"]),
            password=lambda: passphrase.reveal().encode(),
        )
    except ssl.SSLError:
        raise SandboxTLSError("the sandbox client key could not be decrypted or does not match") from None
    assert_verifying(context)
    return context


__all__ = [
    "CLIENT_FILES",
    "PASSPHRASE_REF",
    "SandboxTLSError",
    "assert_verifying",
    "build_client_context",
    "check_key_file",
    "client_paths",
]
