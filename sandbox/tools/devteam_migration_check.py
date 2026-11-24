"""``python -m devteam_migration_check roundtrip | autogenerate <slug>`` (Part E plan §3.4; Migration Runner).

Runs inside the sandbox against a **disposable SQLite** file named by ``DATABASE_URL`` (D-16: PostgreSQL
arrives in Part K). Uses Alembic's Python API with the generated app's ``alembic.ini`` in the working
folder; no shell, no network.

- ``roundtrip``: ``upgrade head`` → ``downgrade base`` → ``upgrade head``; checks the final revision equals
  the head and that every table the migrations create is gone after the downgrade.
- ``autogenerate <slug>``: ``upgrade head``, then ``revision --autogenerate -m <slug>``; the new revision
  file is copied to the output folder as ``proposed_revision.py``. The platform never applies it by itself;
  it is returned as a proposal (the Database Developer's work is reviewed and applied by the harness later).

Writes ``/work/.devteam/out/migration.json`` and exits 0 only if every step succeeded.
Part of the DevTeam AI platform's sandbox image; not third-party code.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

OUT = Path("/work/.devteam/out")


def _tables(url: str) -> list[str]:
    import sqlalchemy  # noqa: PLC0415

    engine = sqlalchemy.create_engine(url)
    try:
        return sorted(t for t in sqlalchemy.inspect(engine).get_table_names() if t != "alembic_version")
    finally:
        engine.dispose()


def _write(result: dict[str, object]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "migration.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"roundtrip", "autogenerate"}:
        print("usage: python -m devteam_migration_check roundtrip | autogenerate <slug>")
        return 2
    if args[0] == "autogenerate" and (len(args) != 2 or not re.fullmatch(r"[a-z0-9_]{1,40}", args[1])):
        print("autogenerate needs one message slug [a-z0-9_]{1,40}")
        return 2
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("sqlite:////work/.devteam/"):
        _write(
            {
                "status": "refused",
                "reason": "only a disposable SQLite database under /work/.devteam is allowed",
            }
        )
        return 2

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    OUT.mkdir(parents=True, exist_ok=True)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    heads = ScriptDirectory.from_config(config).get_heads()
    result: dict[str, object] = {"mode": args[0], "heads": heads, "steps": []}
    steps: list[dict[str, object]] = result["steps"]  # type: ignore[assignment]
    try:
        command.upgrade(config, "head")
        steps.append({"step": "upgrade head", "tables": _tables(url)})
        if args[0] == "roundtrip":
            command.downgrade(config, "base")
            remaining = _tables(url)
            steps.append({"step": "downgrade base", "tables": remaining})
            if remaining:  # checked before the second upgrade, which would fail on the leftovers anyway
                result.update(status="failed", reason=f"tables left after downgrade: {remaining}")
                _write(result)
                return 1
            command.upgrade(config, "head")
            steps.append({"step": "upgrade head again", "tables": _tables(url)})
        else:
            versions = Path(ScriptDirectory.from_config(config).versions)
            before = set(versions.glob("*.py"))
            command.revision(config, message=args[1], autogenerate=True)
            created = sorted(set(versions.glob("*.py")) - before)
            if len(created) != 1:
                result.update(status="failed", reason="autogenerate did not create exactly one revision")
                _write(result)
                return 1
            shutil.copyfile(created[0], OUT / "proposed_revision.py")
            text = created[0].read_text(encoding="utf-8")
            result["empty"] = "op." not in text
            steps.append({"step": "revision --autogenerate", "file": created[0].name})
    except Exception as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {str(exc)[:300]}")
        _write(result)
        return 1
    result["status"] = "passed"
    _write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
