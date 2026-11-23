"""Export the JSON Schema of every shared contract (HS-06, roadmap §13 C.1).

Usage:
    uv run python scripts/export_contract_schemas.py          # (re)write the schema files
    uv run python scripts/export_contract_schemas.py --check  # exit 1 if any file is missing or stale

The committed files under ``packages/core/src/devteam_core/contracts/schemas/`` are the contract
snapshot. ``packages/core/tests/test_contract_schemas.py`` fails when they drift from the code.
"""

from __future__ import annotations

import argparse

from devteam_core.contracts import SCHEMA_DIR, export_json_schemas, render_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="verify only; do not write")
    args = parser.parse_args(argv)

    wanted = {name: render_schema(schema) for name, schema in export_json_schemas().items()}
    existing = {p.name: p.read_text(encoding="utf-8") for p in SCHEMA_DIR.glob("*.json")}
    stale = sorted(n for n, text in wanted.items() if existing.get(n) != text)
    orphaned = sorted(set(existing) - set(wanted))

    if args.check:
        for name in stale:
            print(f"stale or missing: {name}")
        for name in orphaned:
            print(f"orphaned: {name}")
        print(f"{len(wanted)} contract schemas; {len(stale)} stale; {len(orphaned)} orphaned")
        return 1 if stale or orphaned else 0

    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name in stale:
        (SCHEMA_DIR / name).write_text(wanted[name], encoding="utf-8", newline="\n")
    for name in orphaned:
        (SCHEMA_DIR / name).unlink()
    print(f"wrote {len(stale)} of {len(wanted)} contract schemas; removed {len(orphaned)} orphaned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
