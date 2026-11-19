"""Per-install local API token (D-29 (a)).

* 256 random bits, created on first start under ``data/`` (gitignored). Part C moves storage to the OS
  keyring via ``SecretsManager`` (D-18).
* Registered with the redaction registry so it can never appear in logs (HC-17).
* Browsers never hold the raw token in a cookie: the session cookie carries an HMAC derived from it, so a
  leaked cookie cannot be replayed as a bearer token, and rotating the token invalidates every session.
"""

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from devteam_core.logging import secret_registry

_TOKEN_BYTES = 32
_MIN_TOKEN_LENGTH = 40


def load_or_create_token(path: Path) -> str:
    """Return the install token, creating it atomically (exclusive create) if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # another process created it first; fall through and read theirs
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < _MIN_TOKEN_LENGTH:
        raise ValueError(f"API token in {path.name} is too short; delete the file to regenerate it")
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
