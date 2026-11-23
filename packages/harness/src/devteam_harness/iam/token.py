"""Per-install local API token (D-29 (a)), stored in the OS credential store (HC-17, D-18, AP-007).

* 256 random bits, created on the API's first start **in the keyring** (Windows Credential Manager).
  It is never written to a file. There is no plaintext fallback: if the keyring is unavailable the API
  refuses to start.
* The Part B location ``data/api_token`` is legacy. While that file exists the API refuses to start and
  asks for the D-18a migration (``scripts/migrate_api_token.py``), which moves the key under the owner's
  four checks and only then deletes the file.
* Registered with the redaction registry so it can never appear in logs.
* Browsers never hold the raw token in a cookie: the session cookie carries an HMAC derived from it, so a
  leaked cookie cannot be replayed as a bearer token, and rotating the token invalidates every session.
"""

import hashlib
import hmac
import secrets
from pathlib import Path

from devteam_core.errors import DevTeamError
from devteam_core.logging import secret_registry
from devteam_harness.secrets import SecretsManager, SecretsUnavailableError

API_TOKEN_REF = "secret://platform/api_token"  # noqa: S105 - a reference, not a secret
_TOKEN_BYTES = 32
MIN_TOKEN_LENGTH = 40


class TokenMigrationRequiredError(DevTeamError):
    code = "auth.migration_required"


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def load_api_token(secret_store: SecretsManager, legacy_path: Path) -> str:
    """Return the install token from the credential store, creating it there on first use."""
    if legacy_path.exists():
        raise TokenMigrationRequiredError(
            f"A plaintext sign-in key file still exists ({legacy_path.name}). Move it into Windows "
            "Credential Manager first: uv run python scripts/migrate_api_token.py"
        )
    stored = secret_store.get(API_TOKEN_REF)
    if stored is None:
        token = generate_token()
        secret_store.put(API_TOKEN_REF, token)
        stored = secret_store.require(API_TOKEN_REF)
        if not stored.matches(token):
            raise SecretsUnavailableError("the credential store did not return the key it was given")
    token = stored.reveal()
    if len(token) < MIN_TOKEN_LENGTH:
        raise SecretsUnavailableError(f"the stored API token ({API_TOKEN_REF}) is too short")
    secret_registry.register(token)
    return token


def session_value(token: str) -> str:
    """Opaque cookie value derived from the token (never the token itself)."""
    value = hmac.new(token.encode(), b"devteam-ui-session-v1", hashlib.sha256).hexdigest()
    secret_registry.register(value)
    return value


def token_matches(candidate: str | None, expected: str) -> bool:
    """Constant-time comparison; ``None``/empty never matches."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())
