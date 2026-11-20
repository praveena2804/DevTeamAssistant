"""D-18a: move the Part B plaintext sign-in key file into the OS credential store (AP-007 conditions).

The four owner checks run **in this order** and the migration stops at the first failure:

1. ``stored``: the credential was stored successfully (and is present afterwards);
2. ``read_back_matches``: reading it back gives exactly the original (constant-time comparison);
3. ``api_authenticates``: the application authenticates a request using the *stored* credential;
4. ``signin_link_works``: the existing sign-in link (built from the original key) still signs in.

Only when all four pass is the plaintext file deleted (and its absence confirmed). On any failure the
file is left untouched. A different key already present in the store is never overwritten. The result
record contains check names, pass/fail and short reasons, never the key, its hash or any part of it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from devteam_core.logging import redact_text, secret_registry
from devteam_harness.secrets.backends import SecretsUnavailableError
from devteam_harness.secrets.manager import SecretsManager

CHECKS = ("stored", "read_back_matches", "api_authenticates", "signin_link_works")
MIN_TOKEN_LENGTH = 40

Status = Literal["migrated", "failed", "nothing_to_migrate"]


@dataclass(frozen=True, slots=True)
class MigrationCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: Status
    checks: tuple[MigrationCheck, ...] = ()
    file_deleted: bool = False
    notes: tuple[str, ...] = field(default=())

    def as_record(self) -> dict[str, Any]:
        """Audit/log representation: names and outcomes only."""
        return {
            "status": self.status,
            "file_deleted": self.file_deleted,
            "checks": {c.name: {"passed": c.passed, "detail": c.detail} for c in self.checks},
            "notes": list(self.notes),
        }


def _describe(exc: BaseException) -> str:
    return redact_text(f"{type(exc).__name__}: {exc}", secret_registry.values())[:200]


def migrate_legacy_token(  # noqa: PLR0911 - the four checks stop at the first failure
    secrets: SecretsManager,
    legacy_path: Path,
    *,
    ref: str,
    api_accepts: Callable[[str], bool],
    signin_link_works: Callable[[str, str], bool],
) -> MigrationResult:
    if not legacy_path.exists():
        return MigrationResult(status="nothing_to_migrate")
    original = legacy_path.read_text(encoding="utf-8").strip()
    if len(original) < MIN_TOKEN_LENGTH:
        return MigrationResult(
            status="failed", notes=("the key file is too short to be a valid key; left as is",)
        )
    secret_registry.register(original)

    results: list[MigrationCheck] = []

    def finish(failed: MigrationCheck | None) -> MigrationResult:
        if failed is not None:
            results.append(failed)
            done = {c.name for c in results}
            results.extend(MigrationCheck(n, passed=False, detail="not run") for n in CHECKS if n not in done)
            return MigrationResult(status="failed", checks=tuple(results), file_deleted=False)
        legacy_path.unlink()
        deleted = not legacy_path.exists()
        return MigrationResult(
            status="migrated" if deleted else "failed", checks=tuple(results), file_deleted=deleted
        )

    # 1. stored
    try:
        existing = secrets.get(ref)
        if existing is not None and not existing.matches(original):
            return finish(
                MigrationCheck("stored", False, "a different key is already stored; not overwritten")
            )
        if existing is None:
            secrets.put(ref, original)
        if not secrets.exists(ref):
            return finish(MigrationCheck("stored", False, "the credential store did not keep the key"))
    except (SecretsUnavailableError, ValueError) as exc:
        return finish(MigrationCheck("stored", False, _describe(exc)))
    results.append(MigrationCheck("stored", True, f"stored in {secrets.backend_description}"))

    # 2. read back matches
    try:
        stored = secrets.get(ref)
    except SecretsUnavailableError as exc:
        return finish(MigrationCheck("read_back_matches", False, _describe(exc)))
    if stored is None or not stored.matches(original):
        return finish(
            MigrationCheck("read_back_matches", False, "the stored value differs from the original")
        )
    results.append(MigrationCheck("read_back_matches", True, "identical (constant-time comparison)"))

    # 3. the application authenticates with the stored credential
    try:
        api_ok = api_accepts(stored.reveal())
    except Exception as exc:
        return finish(MigrationCheck("api_authenticates", False, _describe(exc)))
    if not api_ok:
        return finish(MigrationCheck("api_authenticates", False, "the API did not accept the stored key"))
    results.append(
        MigrationCheck("api_authenticates", True, "authenticated request accepted; wrong key refused")
    )

    # 4. the existing sign-in link still works
    try:
        link_ok = signin_link_works(original, stored.reveal())
    except Exception as exc:
        return finish(MigrationCheck("signin_link_works", False, _describe(exc)))
    if not link_ok:
        return finish(MigrationCheck("signin_link_works", False, "signing in with the existing link failed"))
    results.append(
        MigrationCheck("signin_link_works", True, "same link; browser sign-in and session accepted")
    )

    return finish(None)
