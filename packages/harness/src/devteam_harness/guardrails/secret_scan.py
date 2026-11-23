"""Detect secrets in text that is about to cross a trust boundary (prompts in, agent outputs out).

Two sources, both high-confidence (no "password: …" heuristics, which would reject legitimate BRD
sentences such as "Password: minimum 8 characters"):

1. every value registered with the redaction registry (keyring secrets, the API token, session values);
2. unmistakable credential shapes (private keys, GitHub/Slack/AWS/OpenAI-style keys, JWTs, bearer
   values, credentials embedded in URLs).

Findings name only the *kind* of secret, never the value.
"""

from __future__ import annotations

import re

from devteam_core.logging import secret_registry

_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}")),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer_credential", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("url_credentials", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@")),
)


def secret_findings(text: str) -> tuple[str, ...]:
    """Kinds of secrets found in ``text`` (empty when clean)."""
    found: list[str] = []
    if any(value in text for value in secret_registry.values()):
        found.append("registered_secret")
    found.extend(kind for kind, pattern in _SHAPES if pattern.search(text))
    return tuple(dict.fromkeys(found))
