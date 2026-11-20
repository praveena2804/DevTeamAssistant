"""Ten-agent enforcement, layer L3 (roadmap §4.4).

Asserts that ``agents/`` contains exactly the ten canonical agent packages and that the root
``pyproject.toml`` lists exactly those ten as workspace members.

Since Part C (C.4) the check is **strict by default**: a missing ``agents/`` directory, a missing or extra
package, a package that is not a workspace member, or a package without its ``AgentSpec`` module is an
error. ``--skeleton`` restores the Part A behaviour (missing ``agents/`` is only a note) for old checkouts.
``--strict`` is accepted for compatibility and is the default.

Usage:  uv run python scripts/check_agent_count.py [--skeleton]
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

# Canonical package directories, in Spec §6 order. Adding an eleventh agent is prohibited (ADR-0002).
CANONICAL_AGENTS: tuple[str, ...] = (
    "brd_analyst",
    "requirements_finalizer",
    "head",
    "architect",
    "database_developer",
    "backend_developer",
    "frontend_developer",
    "tester",
    "debugger_reviewer",
    "documenter",
)
EXPECTED_COUNT = 10
_IGNORED = {"__pycache__"}


def _workspace_agent_members(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
    return [m.split("/", 1)[1] for m in members if isinstance(m, str) and m.startswith("agents/")]


def check(root: Path, *, strict: bool = True) -> tuple[list[str], list[str]]:
    """Return ``(errors, notes)``."""
    errors: list[str] = []
    notes: list[str] = []
    if len(CANONICAL_AGENTS) != EXPECTED_COUNT or len(set(CANONICAL_AGENTS)) != EXPECTED_COUNT:
        errors.append("canonical agent list must contain exactly ten unique names")

    agents_dir = root / "agents"
    members = _workspace_agent_members(root / "pyproject.toml")
    if not agents_dir.is_dir():
        if strict:
            errors.append("agents/ directory is missing (required from Part C)")
        else:
            notes.append("skeleton mode: agents/ not present yet (activated in Part C)")
        if members:
            errors.append(f"pyproject.toml lists agent members without an agents/ directory: {members}")
        return errors, notes

    found = sorted(
        p.name
        for p in agents_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _IGNORED
    )
    expected = sorted(CANONICAL_AGENTS)
    if found != expected:
        extra = sorted(set(found) - set(expected))
        missing = sorted(set(expected) - set(found))
        errors.append(
            f"agents/ must contain exactly the ten canonical packages; extra={extra} missing={missing}"
        )
    for name in found:
        if name in CANONICAL_AGENTS and not (agents_dir / name / "pyproject.toml").is_file():
            errors.append(f"agents/{name} is not a workspace package (no pyproject.toml)")
        spec = agents_dir / name / "src" / "devteam_agents" / name / "spec.py"
        if name in CANONICAL_AGENTS and not spec.is_file():
            errors.append(f"agents/{name} has no AgentSpec module (src/devteam_agents/{name}/spec.py)")
    if sorted(members) != expected:
        errors.append(
            f"root pyproject.toml must list exactly the ten agents as members; found {sorted(members)}"
        )
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ten-agent enforcement (L3)")
    parser.add_argument("--strict", action="store_true", help="(default since Part C) kept for compatibility")
    parser.add_argument("--skeleton", action="store_true", help="Part A mode: missing agents/ is a note")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors, notes = check(args.root, strict=not args.skeleton)
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"error: {error}")
    print("agent count check: " + ("FAILED" if errors else "passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
