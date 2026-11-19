"""Prefixed, time-sortable identifiers (``<prefix>_<ULID>``).

A ULID is 48 bits of millisecond timestamp followed by 80 random bits, encoded as 26 Crockford base32
characters. Lexicographic order equals creation order (to millisecond precision).
"""

import re
import secrets
from datetime import UTC, datetime

from devteam_core.clock import Clock, system_clock

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {ch: i for i, ch in enumerate(_CROCKFORD)}
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")
_ID_RE = re.compile(r"^(?P<prefix>[a-z][a-z0-9]{1,11})_(?P<ulid>[0-7][0-9A-HJKMNP-TV-Z]{25})$")
_MAX_TIMESTAMP_MS = (1 << 48) - 1


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(clock: Clock = system_clock) -> str:
    timestamp_ms = int(clock.now().timestamp() * 1000)
    if not 0 <= timestamp_ms <= _MAX_TIMESTAMP_MS:
        raise ValueError("timestamp outside the ULID range")
    randomness = int.from_bytes(secrets.token_bytes(10), "big")
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)


def new_id(prefix: str, clock: Clock = system_clock) -> str:
    """Return a new identifier such as ``proj_01JD3...``.

    ``prefix`` must be 2-12 lowercase alphanumerics starting with a letter.
    """
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"invalid id prefix {prefix!r}")
    return f"{prefix}_{new_ulid(clock)}"


def is_valid_id(value: str, prefix: str | None = None) -> bool:
    match = _ID_RE.fullmatch(value)
    if match is None:
        return False
    return prefix is None or match.group("prefix") == prefix


def id_timestamp(value: str) -> datetime:
    """Return the creation time encoded in an identifier."""
    match = _ID_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"not a valid identifier: {value!r}")
    timestamp_ms = 0
    for ch in match.group("ulid")[:10]:
        timestamp_ms = (timestamp_ms << 5) | _DECODE[ch]
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
