"""Static rules for the ten agent packages that import rules cannot express (CLAUDE.md §5; AC-C-04).

Import-linter and Ruff banned-API stop agents importing I/O libraries, other agents and harness
internals. This AST check adds an **allowlist** and the built-ins:

1. imports: only ``devteam_core``, ``devteam_harness.agent_sdk`` (and only the names in its ``__all__``),
   the agent's own package, and a small list of pure standard-library modules;
2. no ``open``, ``eval``, ``exec``, ``compile``, ``__import__``, ``getattr``/``setattr``/``delattr``,
   ``vars``, ``globals``, ``locals``, ``breakpoint``, ``input``;
3. no access to private or dunder attributes (``obj._x``, ``fn.__closure__``), so the access objects in
   ``AgentContext`` cannot be unwrapped;
4. no model names (``qwen…``, ``llama…``): agents request an operation and the gateway picks the model
   (MODEL_ROUTING rule 11).

Usage:  uv run python scripts/check_agent_rules.py [--root PATH]
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

STDLIB_ALLOWED = frozenset(
    {
        "__future__",
        "typing",
        "collections.abc",
        "enum",
        "dataclasses",
        "re",
        "json",
        "datetime",
        "math",
        "decimal",
        "functools",
        "itertools",
        "textwrap",
        "string",
    }
)
BANNED_CALLS = frozenset(
    {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "vars",
        "globals",
        "locals",
        "breakpoint",
        "input",
    }
)
MODEL_TAG = re.compile(
    r"(?i)\b(qwen|llama|mistral|mixtral|phi[0-9]|gemma|deepseek|codellama|starcoder|nomic-embed)[\w.-]*"
)
SDK = "devteam_harness.agent_sdk"


def _sdk_names() -> frozenset[str]:
    from devteam_harness import agent_sdk  # noqa: PLC0415 - only needed when checking

    return frozenset(agent_sdk.__all__)


def _module_allowed(module: str, own: str) -> bool:
    if module in STDLIB_ALLOWED or module == SDK:
        return True
    return any(module == p or module.startswith(p + ".") for p in ("devteam_core", f"devteam_agents.{own}"))


def check_file(path: Path, own: str, sdk_names: frozenset[str]) -> list[str]:  # noqa: PLR0912
    rel = path.as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno}: cannot be parsed"]
    problems: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name, own):
                    problems.append(f"{rel}:{line}: import of '{alias.name}' is not allowed in an agent")
                elif alias.name == SDK:
                    problems.append(f"{rel}:{line}: import names from {SDK}, not the module itself")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > 1:
                    problems.append(f"{rel}:{line}: relative import leaves the agent package")
                continue
            module = node.module or ""
            if not _module_allowed(module, own):
                problems.append(f"{rel}:{line}: import from '{module}' is not allowed in an agent")
            elif module == SDK:
                for alias in node.names:
                    if alias.name not in sdk_names:
                        problems.append(f"{rel}:{line}: '{alias.name}' is not part of the agent SDK")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            problems.append(f"{rel}:{line}: call to '{node.func.id}' is not allowed in an agent")
        elif isinstance(node, ast.Name) and node.id in BANNED_CALLS and isinstance(node.ctx, ast.Load):
            problems.append(f"{rel}:{line}: use of '{node.id}' is not allowed in an agent")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            problems.append(f"{rel}:{line}: private attribute '{node.attr}' is not allowed in an agent")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and MODEL_TAG.search(node.value):
            problems.append(f"{rel}:{line}: model names belong in config, not in an agent")
    return sorted(set(problems))


def check(root: Path) -> list[str]:
    agents = root / "agents"
    if not agents.is_dir():
        return ["agents/ directory is missing"]
    names = _sdk_names()
    problems: list[str] = []
    for package in sorted(p for p in agents.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        for path in sorted(package.rglob("*.py")):
            problems.extend(check_file(path, package.name, names))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent static rules (AST)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    problems = check(args.root)
    for problem in problems:
        print(f"error: {problem}")
    print("agent rules check: " + ("FAILED" if problems else "passed"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
