"""C.4 bypass prevention for agents (AC-C-04): import-linter, Ruff banned-API and the AST rules.

Each layer is run on the real repository (must pass) and on a temporary copy with a **planted
violation** (must fail). The planted copies never touch the real agents.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.security


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load("check_agent_rules")
OWN = "backend_developer"


def _copy_agents(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    return root


def _plant(root: Path, code: str, name: str = "planted.py") -> Path:
    path = root / "agents" / OWN / "src" / "devteam_agents" / OWN / name
    path.write_text(code, encoding="utf-8", newline="\n")
    return path


# ------------------------------------------------------------------------------------- AST rules


def test_the_real_agents_pass_the_ast_rules() -> None:
    assert rules.check(REPO_ROOT) == []
    assert rules.main(["--root", str(REPO_ROOT)]) == 0


PLANTED = [
    ("handle = open('C:/Windows/win.ini')", "call to 'open'"),
    ("eval('1+1')", "call to 'eval'"),
    ("exec('import os')", "call to 'exec'"),
    ("m = __import__('subprocess')", "call to '__import__'"),
    ("f = getattr(object, 'x')", "call to 'getattr'"),
    ("g = vars()", "call to 'vars'"),
    ("b = breakpoint", "use of 'breakpoint'"),
    ("def f(ctx):\n    return ctx.tools._call.__closure__", "private attribute '_call'"),
    ("import os", "import of 'os'"),
    ("import pathlib", "import of 'pathlib'"),
    ("from subprocess import run", "import from 'subprocess'"),
    ("import httpx", "import of 'httpx'"),
    ("from devteam_state.repositories import runs", "import from 'devteam_state.repositories'"),
    ("from devteam_harness.tools import ToolLayer", "import from 'devteam_harness.tools'"),
    (
        "from devteam_harness.agent_sdk.registry import AgentRegistry",
        "import from 'devteam_harness.agent_sdk.registry'",
    ),
    ("from devteam_harness.agent_sdk import AgentRegistry", "'AgentRegistry' is not part of the agent SDK"),
    ("import devteam_harness.agent_sdk", "import names from devteam_harness.agent_sdk"),
    ("from devteam_agents.tester import SPEC", "import from 'devteam_agents.tester'"),
    ("from .. import tester", "relative import leaves the agent package"),
    ("MODEL = 'qwen2.5-coder:3b'", "model names belong in config"),
]


@pytest.mark.parametrize(("code", "message"), PLANTED, ids=[m for _, m in PLANTED])
def test_each_planted_violation_is_caught_by_the_ast_rules(tmp_path: Path, code: str, message: str) -> None:
    root = _copy_agents(tmp_path)
    assert rules.check(root) == []
    _plant(root, code + "\n")
    problems = rules.check(root)
    assert any(message in p for p in problems), problems
    assert rules.main(["--root", str(root)]) == 1


def test_allowed_code_is_not_flagged(tmp_path: Path) -> None:
    root = _copy_agents(tmp_path)
    _plant(
        root,
        (
            "from __future__ import annotations\n"
            "import json\n"
            "from devteam_core.contracts.statuses import AgentRole\n"
            "from devteam_harness.agent_sdk import AgentContext, DataItem\n"
            "from devteam_agents.backend_developer.spec import SPEC\n"
            "from . import spec\n"
            "def run(ctx: AgentContext) -> str:\n"
            "    answer = ctx.llm.generate('generate_code', DataItem('brd_chunk', 'x'))\n"
            "    return json.dumps({'role': AgentRole.BACKEND_DEVELOPER.value, 'text': answer.text})\n"
        ),
    )
    assert rules.check(root) == []


# ------------------------------------------------------------------------------- Ruff banned-API


def _ruff(root: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )


def test_ruff_banned_api_passes_on_the_real_agents() -> None:
    result = _ruff(REPO_ROOT, REPO_ROOT / "agents")
    assert result.returncode == 0, result.stdout


def test_ruff_banned_api_catches_planted_imports(tmp_path: Path) -> None:
    root = _copy_agents(tmp_path)
    planted = _plant(
        root,
        (
            "import os\nimport subprocess\nimport socket\nimport sqlite3\nimport httpx\n"
            "from devteam_state import models\nfrom devteam_harness.llm_gateway import LLMGateway\n"
            "from devteam_harness.secrets import SecretsManager\n"
        ),
    )
    result = _ruff(root, planted)
    assert result.returncode == 1
    for banned in (
        "os",
        "subprocess",
        "socket",
        "sqlite3",
        "httpx",
        "devteam_state",
        "devteam_harness.llm_gateway",
        "devteam_harness.secrets",
    ):
        assert f"`{banned}` is banned" in result.stdout, (banned, result.stdout)


# ---------------------------------------------------------------------------------- import-linter


def _lint_imports(root: Path) -> subprocess.CompletedProcess[str]:
    src = root / "agents" / OWN / "src"
    env = {
        **os.environ,
        "PYTHONPATH": str(src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from importlinter.cli import lint_imports_command as c; "
            "sys.argv=['lint-imports','--config',sys.argv[1],'--no-cache']; c()",
            str(root / "pyproject.toml"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )


def test_import_linter_passes_on_the_real_repository() -> None:
    result = _lint_imports(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


@pytest.mark.parametrize(
    ("code", "contract"),
    [
        (
            "import devteam_state.models\n",
            "agents may import only devteam_core and devteam_harness.agent_sdk",
        ),
        ("from devteam_harness.tools import layer\n", "agents may import only devteam_core"),
        ("import httpx\n", "only llm_gateway.providers may import HTTP clients"),
        ("from devteam_agents.tester import spec\n", "the ten agents are independent of each other"),
    ],
)
def test_import_linter_catches_planted_imports(tmp_path: Path, code: str, contract: str) -> None:
    root = _copy_agents(tmp_path)
    _plant(root, code, name="planted_linter.py")
    result = _lint_imports(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{contract}" in result.stdout and "BROKEN" in result.stdout
