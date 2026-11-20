"""Secret storage backends (HC-17, D-18).

``KeyringBackend`` is the only production backend: the OS keyring (Windows Credential Manager on this
platform). It **fails closed**: if the active keyring is not a recognised secure OS backend (for example
a plaintext or "encrypted file" keyring from ``keyrings.alt``, the ``fail``/``null`` backends, a chainer,
or anything unknown selected through ``PYTHON_KEYRING_BACKEND``), construction raises and nothing is
stored. There is no file fallback.

``MemoryBackend`` keeps values in process memory and exists for the automated test suite only; the
factory refuses it outside the ``test`` profile.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from devteam_core.errors import DevTeamError

# Modules of keyring backends that store secrets in an OS-protected credential store.
SECURE_BACKEND_MODULES = (
    "keyring.backends.Windows",  # Windows Credential Manager
    "keyring.backends.macOS",  # macOS Keychain
    "keyring.backends.SecretService",  # GNOME keyring / KWallet via Secret Service
    "keyring.backends.libsecret",
    "keyring.backends.kwallet",
)


class SecretsUnavailableError(DevTeamError):
    code = "secrets.unavailable"


class SecretBackend(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


def _is_secure(backend: object) -> bool:
    module = type(backend).__module__
    return any(module == allowed or module.startswith(allowed + ".") for allowed in SECURE_BACKEND_MODULES)


class KeyringBackend:
    def __init__(self, service_name: str) -> None:
        try:
            import keyring  # noqa: PLC0415 - imported lazily so tests can substitute the active backend
        except ImportError:  # pragma: no cover - dependency is declared
            raise SecretsUnavailableError("the keyring package is not installed") from None
        backend: Any = keyring.get_keyring()
        if not _is_secure(backend):
            raise SecretsUnavailableError(
                f"no secure OS credential store is available (active keyring backend "
                f"'{type(backend).__module__}.{type(backend).__name__}' is not allowed); secrets are never "
                "kept in plaintext files"
            )
        self._backend = backend
        self._service = service_name

    @property
    def description(self) -> str:
        return f"{type(self._backend).__module__}.{type(self._backend).__name__}"

    def get(self, key: str) -> str | None:
        try:
            value = self._backend.get_password(self._service, key)
        except Exception:
            raise SecretsUnavailableError(f"could not read '{key}' from the credential store") from None
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        try:
            self._backend.set_password(self._service, key, value)
        except Exception:
            raise SecretsUnavailableError(f"could not store '{key}' in the credential store") from None

    def delete(self, key: str) -> None:
        try:
            self._backend.delete_password(self._service, key)
        except Exception as exc:
            if type(exc).__name__ == "PasswordDeleteError":
                return  # already absent
            raise SecretsUnavailableError(f"could not delete '{key}' from the credential store") from None


class MemoryBackend:
    """In-process store for the automated tests (refused outside the ``test`` profile)."""

    description = "memory (tests only)"

    def __init__(self, seed: Mapping[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(seed or {})

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        self._values[key] = value

    def delete(self, key: str) -> None:
        self._values.pop(key, None)
