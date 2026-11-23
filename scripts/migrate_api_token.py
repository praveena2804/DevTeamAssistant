"""D-18a: move the Part B sign-in key file into Windows Credential Manager (owner conditions, AP-007).

Usage:  uv run python scripts/migrate_api_token.py

Runs the four checks in order (stored → read back matches → API authenticates with the stored key →
existing sign-in link still works). Only if all four pass is ``data/api_token`` deleted. If any check
fails, the migration stops, the file is kept unchanged, and the exit code is 1.

The key itself is never printed, logged or written to the audit trail; only check names and outcomes.
The result is recorded as audit event ``secret.migrated`` (outcome success / error).
"""

from __future__ import annotations

import sys

from devteam_api.token_checks import api_authenticates, signin_link_works
from devteam_core.config import Settings, load_settings
from devteam_core.logging import configure_logging, get_logger
from devteam_harness import audit
from devteam_harness.iam import API_TOKEN_REF
from devteam_harness.iam.principals import ServiceName, ServicePrincipal
from devteam_harness.secrets import MigrationResult, migrate_legacy_token, secrets_manager_from_settings
from devteam_state import create_sqlite_engine, ensure_state_schema, session_factory

_log = get_logger("migrate_api_token")


def run(settings: Settings) -> MigrationResult:
    secrets = secrets_manager_from_settings(settings)  # fails closed if no secure keyring
    result = migrate_legacy_token(
        secrets,
        settings.api_token_path,
        ref=API_TOKEN_REF,
        api_accepts=api_authenticates(settings),
        signin_link_works=signin_link_works(settings),
    )
    if result.status != "nothing_to_migrate":
        ensure_state_schema(settings)
        engine = create_sqlite_engine(
            settings.state_db_path, busy_timeout_ms=settings.database.busy_timeout_ms
        )
        try:
            with session_factory(engine).begin() as db:
                audit.record(
                    db,
                    action="secret.migrated",
                    outcome="success" if result.status == "migrated" else "error",
                    principal_id=ServicePrincipal(ServiceName.SECRETS_MIGRATION).id,
                    target_type="secret",
                    target_id=API_TOKEN_REF,
                    details=result.as_record(),
                )
        finally:
            engine.dispose()
    _log.info("api_token_migration", **result.as_record())
    return result


def main() -> int:
    settings = load_settings()
    configure_logging(settings)
    result = run(settings)
    if result.status == "nothing_to_migrate":
        print("No plaintext key file found; nothing to migrate.")
        return 0
    for number, check in enumerate(result.checks, start=1):
        mark = "PASS" if check.passed else ("----" if check.detail == "not run" else "FAIL")
        print(f"  {number}. [{mark}] {check.name}: {check.detail}")
    for note in result.notes:
        print(f"  note: {note}")
    if result.status == "migrated":
        print(
            "Migration complete: the key is in Windows Credential Manager and the plaintext file was deleted."
        )
        return 0
    print(
        "Migration STOPPED: the original key file was kept unchanged. Nothing was deleted.", file=sys.stderr
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
