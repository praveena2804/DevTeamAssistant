"""Secret redaction for log records (HC-17 foundation, HS-01).

Three independent mechanisms; any one of them is enough to redact a value:

1. **Registered secret values.** ``SecretsManager`` (Part C) registers every secret it hands out. Any
   occurrence of a registered value anywhere in a log record is replaced.
2. **Sensitive key names.** Values under keys such as ``password``, ``token`` or ``authorization`` are
   replaced wholesale, at any nesting depth.
3. **Known token shapes.** GitHub tokens, bearer tokens, credentials embedded in URLs, private-key blocks,
   JWTs and similar patterns are replaced inside free text.

Redaction is applied to the final event dict immediately before rendering, so it also covers
exception tracebacks and values bound through context variables.
"""

import re
import threading
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

REDACTED = "[REDACTED]"
MIN_SECRET_LENGTH = 6
_MAX_DEPTH = 12

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "authorization",
    "cookie",
    "privatekey",
    "credential",
    "accesskey",
    "connectionstring",
    "databaseurl",
    "dsn",
)
# Keys that contain a sensitive word but only carry non-secret metadata.
_SAFE_KEYS = frozenset({"token_count", "tokens", "prompt_tokens", "completion_tokens", "max_tokens"})

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private key blocks
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        REDACTED,
    ),
    # GitHub tokens (classic and fine-grained)
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), REDACTED),
    # Slack, AWS access key ids, OpenAI/Anthropic-style keys
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), REDACTED),
    # JSON Web Tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), REDACTED),
    # Authorization: Bearer/Basic <value>
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    # Credentials embedded in URLs: scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"), r"\1:" + REDACTED + "@"),
    # key=value / key: value pairs in free text for sensitive keys
    (
        re.compile(
            r"(?i)\b((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)"
            r"\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
        ),
        r"\1" + REDACTED,
    ),
)


class SecretRegistry:
    """Thread-safe set of literal secret values that must never be emitted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: frozenset[str] = frozenset()

    def register(self, value: str) -> None:
        if not isinstance(value, str) or len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"secret values must be strings of at least {MIN_SECRET_LENGTH} characters")
        with self._lock:
            self._values = self._values | {value}

    def unregister(self, value: str) -> None:
        with self._lock:
            self._values = self._values - {value}

    def clear(self) -> None:
        with self._lock:
            self._values = frozenset()

    def values(self) -> frozenset[str]:
        return self._values

    def __len__(self) -> int:
        return len(self._values)


secret_registry = SecretRegistry()


def _normalise_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: object) -> bool:
    if str(key).lower() in _SAFE_KEYS:
        return False
    normalised = _normalise_key(key)
    # Counters such as input_tokens / token_count / eval_count carry no secret material.
    if normalised.endswith(("tokens", "count")):
        return False
    return any(part in normalised for part in _SENSITIVE_KEY_PARTS)


def redact_text(text: str, registered: Iterable[str] = ()) -> str:
    # Longest values first so that a secret containing another secret is fully removed.
    for value in sorted(registered, key=len, reverse=True):
        if value in text:
            text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(  # noqa: PLR0911 - one return per value type keeps the dispatch auditable
    value: Any, registered: frozenset[str] | None = None, _depth: int = 0
) -> Any:
    secrets_ = secret_registry.values() if registered is None else registered
    if _depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value, secrets_)
    if isinstance(value, bytes):
        return redact_text(value.decode("utf-8", errors="replace"), secrets_)
    if isinstance(value, Mapping):
        return {
            key: (REDACTED if is_sensitive_key(key) else redact_value(item, secrets_, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(item, secrets_, _depth + 1) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    # Unknown objects are stringified so that their repr cannot smuggle a secret past redaction.
    return redact_text(str(value), secrets_)


def redact_event(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact every field of the event in place."""
    registered = secret_registry.values()
    for key in list(event_dict.keys()):
        if is_sensitive_key(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact_value(event_dict[key], registered)
    return event_dict
