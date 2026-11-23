"""Test-only network jail for a worker subprocess (Part D E2E-2): loopback connections only.

Put this directory first on ``PYTHONPATH`` of the process under test. Any attempt to connect to a
non-loopback address is refused and recorded in the file named by ``E2E_NET_JAIL_LOG`` (one line per
attempt), so the test can assert that a full run made no outbound connection. It does not replace or
imitate any service: loopback traffic (the local Ollama, nothing else) passes through untouched.
"""

from __future__ import annotations

import ipaddress
import os
import socket

_LOG = os.environ.get("E2E_NET_JAIL_LOG")
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_local(address: object) -> bool:
    if isinstance(address, tuple) and address:
        host = str(address[0])
        if host in {"localhost"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    return True  # AF_UNIX and similar local sockets


def _record(address: object) -> None:
    if _LOG:
        with open(_LOG, "a", encoding="utf-8") as handle:  # noqa: PTH123 - test helper, stdlib only
            handle.write(f"{address!r}\n")


def _connect(self: socket.socket, address: object) -> None:
    if not _is_local(address):
        _record(address)
        raise OSError("outbound network access is blocked in this test (loopback only)")
    _real_connect(self, address)  # type: ignore[arg-type]


def _connect_ex(self: socket.socket, address: object) -> int:
    if not _is_local(address):
        _record(address)
        return 111  # ECONNREFUSED
    return _real_connect_ex(self, address)  # type: ignore[arg-type]


socket.socket.connect = _connect  # type: ignore[method-assign,assignment]
socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign,assignment]
