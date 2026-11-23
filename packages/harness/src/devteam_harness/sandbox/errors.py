"""Sandbox outcomes that are not ordinary failures of the code under test.

- :class:`SandboxBlockedError`: a mandatory control is unavailable (engine down, TLS material missing,
  Docker Desktop detected, image missing or altered). The work is **Blocked**; there is no fallback and
  no automatic retry. Category ``fatal`` so the harness never retries it on its own.
- Waiting for memory or for the LLM/sandbox lease uses the existing ``ResourceUnavailableError``
  (``waiting for resources``), as in Part D.
"""

from __future__ import annotations

from devteam_harness.errors import FatalError


class SandboxBlockedError(FatalError):
    """A sandbox safety precondition failed. Fail closed: nothing runs, nothing runs on the host."""


__all__ = ["SandboxBlockedError"]
