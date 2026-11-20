"""HC-17 secrets management (HS-09; D-18 keyring only, D-18a legacy key migration)."""

from devteam_harness.secrets.backends import (
    SECURE_BACKEND_MODULES,
    KeyringBackend,
    MemoryBackend,
    SecretBackend,
    SecretsUnavailableError,
)
from devteam_harness.secrets.manager import (
    SecretsManager,
    SecretValue,
    secret_key,
    secrets_manager_from_settings,
)
from devteam_harness.secrets.migration import MigrationCheck, MigrationResult, migrate_legacy_token

__all__ = [
    "SECURE_BACKEND_MODULES",
    "KeyringBackend",
    "MemoryBackend",
    "MigrationCheck",
    "MigrationResult",
    "SecretBackend",
    "SecretValue",
    "SecretsManager",
    "SecretsUnavailableError",
    "migrate_legacy_token",
    "secret_key",
    "secrets_manager_from_settings",
]
