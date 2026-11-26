"""Part G security: the Capability Catalog and the Feasibility Gate are deterministic (no model,
network, sandbox, vector store, shell or file-system writes of their own), and the feasibility API keeps
projects apart.

The import-linter contract enforces the package boundary; this AST scan is the second, independent layer and
has a negative control proving it detects a planted import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from devteam_services import catalog, feasibility

pytestmark = pytest.mark.security

FORBIDDEN = (
    "devteam_harness.llm_gateway", "devteam_harness.rag", "devteam_harness.sandbox.engine",
    "devteam_harness.sandbox.service", "httpx", "requests", "docker", "chromadb", "socket", "subprocess",
    "urllib", "http", "ollama", "shutil",
)  # fmt: skip
PACKAGES = (Path(catalog.__file__).parent, Path(feasibility.__file__).parent)


def forbidden_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        found += [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
    return found


def test_catalog_and_feasibility_code_never_imports_a_model_network_or_sandbox_module() -> None:
    files = [f for package in PACKAGES for f in sorted(package.glob("*.py"))]
    assert len(files) >= 5  # catalog + feasibility/__init__, mapping, rules, render
    for path in files:
        assert forbidden_imports(path.read_text(encoding="utf-8")) == [], path.name


@pytest.mark.parametrize(
    "planted",
    [
        "import httpx",
        "from devteam_harness.llm_gateway import LLMGateway",
        "from devteam_harness.sandbox.service import SandboxService",
        "import subprocess",
        "from socket import create_connection",
    ],
)
def test_negative_control_the_scan_finds_a_planted_import(planted: str) -> None:
    source = (PACKAGES[1] / "rules.py").read_text(encoding="utf-8") + "\n" + planted + "\n"
    assert forbidden_imports(source)
