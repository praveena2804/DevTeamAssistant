"""D.1 import boundaries for orchestration and the worker (Part D plan §3.1), each with a negative control.

Every new import-linter contract passes on the real repository and **breaks** when a violating import is
planted in a temporary copy of the offending package (the copy is put first on ``PYTHONPATH`` so it
shadows the installed package; the real sources are never touched). The new Ruff bans for agents are
checked the same way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.security

PACKAGE_SRC = {
    "devteam_core": REPO_ROOT / "packages" / "core" / "src",
    "devteam_state": REPO_ROOT / "packages" / "state" / "src",
    "devteam_harness": REPO_ROOT / "packages" / "harness" / "src",
    "devteam_services": REPO_ROOT / "packages" / "services" / "src",
    "devteam_api": REPO_ROOT / "apps" / "api" / "src",
    "devteam_orchestration": REPO_ROOT / "packages" / "orchestration" / "src",
    "devteam_worker": REPO_ROOT / "apps" / "worker" / "src",
    "devteam_agents": REPO_ROOT / "agents" / "backend_developer" / "src",
}
LOWER = "no orchestration, worker or LangGraph below the orchestration layer"
ORCH = "orchestration must not depend on apps"
API = "the API never runs graphs or agents directly"
WORKER = "the worker reaches agents only through orchestration"
HTTP = "only llm_gateway.providers may import HTTP clients (HC-02)"
FAKE = "runs never use the fake model provider (D-38)"  # D.2: owner ruling, real AI providers only


def _lint_imports(extra_path: Path | None) -> subprocess.CompletedProcess[str]:
    path = [str(extra_path)] if extra_path else []
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([*path, os.environ.get("PYTHONPATH", "")]),
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from importlinter.cli import lint_imports_command as c; "
            "sys.argv=['lint-imports','--config',sys.argv[1],'--no-cache']; c()",
            str(REPO_ROOT / "pyproject.toml"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )


def _planted_copy(tmp_path: Path, package: str, code: str) -> Path:
    src = tmp_path / "src"
    shutil.copytree(PACKAGE_SRC[package], src, ignore=shutil.ignore_patterns("__pycache__"))
    if package == "devteam_agents":
        target = src / "devteam_agents" / "backend_developer" / "planted_d1.py"
    else:
        target = src / package / "planted_d1.py"
    target.write_text(code, encoding="utf-8", newline="\n")
    return src


def test_import_linter_passes_with_the_new_contracts() -> None:
    result = _lint_imports(None)
    assert result.returncode == 0, result.stdout + result.stderr
    flat = " ".join(result.stdout.split())
    for contract in (LOWER, ORCH, API, WORKER, FAKE):
        assert f"{contract} KEPT" in flat, contract
    assert "0 broken" in flat


@pytest.mark.parametrize(
    ("package", "code", "contract"),
    [
        ("devteam_core", "import devteam_orchestration\n", LOWER),
        ("devteam_state", "import langgraph\n", LOWER),
        ("devteam_harness", "from devteam_orchestration import safety\n", LOWER),
        ("devteam_harness", "import langchain_core\n", LOWER),
        ("devteam_services", "import devteam_worker\n", LOWER),
        ("devteam_agents", "import langgraph\n", LOWER),
        ("devteam_agents", "import langsmith\n", LOWER),
        ("devteam_orchestration", "import devteam_api\n", ORCH),
        ("devteam_orchestration", "import devteam_worker\n", ORCH),
        ("devteam_orchestration", "import httpx\n", HTTP),
        ("devteam_api", "import langgraph\n", API),
        ("devteam_api", "from devteam_agents.head import spec\n", API),
        ("devteam_worker", "from devteam_agents.head import spec\n", WORKER),
        ("devteam_worker", "import httpx\n", HTTP),
        ("devteam_orchestration", "from devteam_harness.llm_gateway.providers import fake\n", FAKE),
        (
            "devteam_orchestration",
            "from devteam_harness.llm_gateway.providers.fake import FakeProvider\n",
            FAKE,
        ),
        ("devteam_worker", "import devteam_harness.llm_gateway.providers.fake\n", FAKE),
        ("devteam_api", "from devteam_harness.llm_gateway.providers.fake import FakeProvider\n", FAKE),
        ("devteam_agents", "from devteam_harness.llm_gateway.providers import fake\n", FAKE),
    ],
)
def test_each_planted_violation_breaks_its_contract(
    tmp_path: Path, package: str, code: str, contract: str
) -> None:
    result = _lint_imports(_planted_copy(tmp_path, package, code))
    assert result.returncode == 1, result.stdout + result.stderr
    flat = " ".join(result.stdout.split())  # import-linter wraps long lines
    assert f"{contract} BROKEN" in flat, result.stdout


def test_ruff_bans_langgraph_in_agents(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    planted = root / "agents" / "backend_developer" / "src" / "devteam_agents" / "backend_developer" / "p.py"
    planted.write_text(
        "import langgraph\nimport langchain_core\nimport langsmith\n"
        "from devteam_orchestration import safety\nimport devteam_worker\n",
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--select",
            "TID251",
            "--config",
            str(root / "agents" / "ruff.toml"),
            str(planted),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )
    assert result.returncode == 1
    for banned in ("langgraph", "langchain_core", "langsmith", "devteam_orchestration", "devteam_worker"):
        assert f"`{banned}` is banned" in result.stdout, (banned, result.stdout)
