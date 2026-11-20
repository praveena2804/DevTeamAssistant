"""``SecretsManager`` (HC-17, HS-09).

- Secrets are addressed by reference (``secret://platform/<name>`` or ``secret://prj_…/<name>``), never
  by value; only references are stored in the database or configuration (``SecretReference``).
- Every value handed out or stored is registered with the redaction registry, so it is removed from
  logs, audit details and traces wherever it appears.
- Values travel as :class:`SecretValue`, whose ``str``/``repr`` never show the value; callers must call
  :meth:`SecretValue.reveal` explicitly at the point of use.
- Errors name the reference, never the value, and never chain the backend's own exception (which could
  carry the value).
"""

from __future__ import annotations

import hmac
import os
import re
from collections.abc import Callable, Mapping

from devteam_core.config import Settings
from devteam_core.errors import ConfigError
from devteam_core.logging import secret_registry
from devteam_core.logging.redaction import MIN_SECRET_LENGTH
from devteam_harness.secrets.backends import (
    KeyringBackend,
    MemoryBackend,
    SecretBackend,
    SecretsUnavailableError,
)

_REF = re.compile(
    r"^secret://(?P<scope>platform|prj_[0-9A-HJKMNP-TV-Z]{26})/(?P<name>[a-z][a-z0-9_.-]{0,63})$"
)
TEST_SEED_PREFIX = "DTTEST_SECRET__"


def secret_key(ref: str) -> str:
    match = _REF.fullmatch(ref)
    if match is None or ".." in match.group("name"):
        raise ValueError(
            "invalid secret reference (expected secret://platform/<name> or secret://prj_…/<name>)"
        )
    return f"{match.group('scope')}/{match.group('name')}"


class SecretValue:
    """A secret that does not reveal itself when printed, logged or formatted."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def matches(self, candidate: str) -> bool:
        return hmac.compare_digest(self._value.encode(), candidate.encode())

    def __repr__(self) -> str:
        return "SecretValue(****)"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return "SecretValue(****)"


class SecretsManager:
    def __init__(self, backend: SecretBackend) -> None:
        self._backend = backend

    @property
    def backend_description(self) -> str:
        return str(getattr(self._backend, "description", type(self._backend).__name__))

    def get(self, ref: str) -> SecretValue | None:
        key = secret_key(ref)  # validate before touching the backend
        value = self._call(lambda: self._backend.get(key), ref, "read")
        if value is None:
            return None
        if len(value) >= MIN_SECRET_LENGTH:
            secret_registry.register(value)
        return SecretValue(value)

    def require(self, ref: str) -> SecretValue:
        value = self.get(ref)
        if value is None:
            raise SecretsUnavailableError(f"secret {ref} is not stored")
        return value

    def exists(self, ref: str) -> bool:
        return self.get(ref) is not None

    def put(self, ref: str, value: str) -> None:
        key = secret_key(ref)
        if not isinstance(value, str) or len(value.strip()) < MIN_SECRET_LENGTH or value != value.strip():
            raise ValueError(
                f"secret {ref}: value must be at least {MIN_SECRET_LENGTH} characters, no padding"
            )
        secret_registry.register(value)
        self._call(lambda: self._backend.set(key, value), ref, "store")

    def delete(self, ref: str) -> None:
        key = secret_key(ref)
        self._call(lambda: self._backend.delete(key), ref, "delete")

    @staticmethod
    def _call[T](operation: Callable[[], T], ref: str, verb: str) -> T:
        try:
            return operation()
        except SecretsUnavailableError:
            raise
        except Exception:
            raise SecretsUnavailableError(f"could not {verb} secret {ref}") from None


def secrets_manager_from_settings(
    settings: Settings, environ: Mapping[str, str] | None = None
) -> SecretsManager:
    """Build the configured manager. Fails closed; the memory backend is for the test profile only."""
    if settings.secrets.backend == "keyring":
        return SecretsManager(KeyringBackend(settings.secrets.service_name))
    if settings.profile != "test":
        raise ConfigError("secrets.backend 'memory' is allowed only in the test profile (D-18)")
    env = os.environ if environ is None else environ
    seed = {
        f"platform/{name[len(TEST_SEED_PREFIX) :].lower()}": value
        for name, value in env.items()
        if name.startswith(TEST_SEED_PREFIX)
    }
    return SecretsManager(MemoryBackend(seed))
