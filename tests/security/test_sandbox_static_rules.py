"""Part E static rules, each with a negative control (Part E plan §3.1, §3.11; AC-E-04, D-50).

1. **No host execution path (AC-E-04):** no non-test platform module (packages, apps, agents) may start a
   process: ``subprocess``, ``os.system``, ``os.exec*``, ``os.spawn*``, ``os.startfile``, ``os.popen``,
   ``asyncio.create_subprocess_*`` or ``pty``. Only owner-run ``scripts/`` and tests may.
2. **TLS verification is never disabled:** no ``verify=False``, ``CERT_NONE``, ``check_hostname = False``,
   ``assert_hostname=False`` or ``_create_unverified_context`` in platform code.
3. The new import-linter contracts hold, and break on planted violations; agents' Ruff config bans
   ``docker`` and ``devteam_harness.sandbox``.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.harness import REPO_ROOT
from tests.security.test_orchestration_static_rules import _lint_imports, _planted_copy

pytestmark = pytest.mark.security

PLATFORM_ROOTS = [REPO_ROOT / "packages", REPO_ROOT / "apps", REPO_ROOT / "agents"]
PROCESS_MODULES = {"subprocess", "pty", "multiprocessing.popen_spawn_win32"}
OS_PROCESS_CALLS = {"system", "popen", "startfile", "posix_spawn", "posix_spawnp", "fork", "forkpty"}
DOCKER = "only sandbox.engine may import the Docker SDK (HC-14)"
ENGINE = "agents, orchestration, API and worker never touch the Docker engine directly"


def platform_modules() -> list[Path]:
    files: list[Path] = []
    for root in PLATFORM_ROOTS:
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(REPO_ROOT).parts)
            if parts & {"tests", "node_modules", "migrations", ".venv", "__pycache__"}:
                continue
            files.append(path)
    return files


def process_violations(source: str) -> list[str]:
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name.split(".")[0] in {"subprocess", "pty"}]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in {"subprocess", "pty"}:
                found.append(node.module)
            if node.module == "os":
                found += [
                    f"os.{a.name}"
                    for a in node.names
                    if a.name in OS_PROCESS_CALLS or a.name.startswith(("exec", "spawn"))
                ]
            if node.module == "asyncio":
                found += [f"asyncio.{a.name}" for a in node.names if a.name.startswith("create_subprocess")]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and (
                node.attr in OS_PROCESS_CALLS or node.attr.startswith(("exec", "spawn"))
            ):
                found.append(f"os.{node.attr}")
            if node.value.id == "asyncio" and node.attr.startswith("create_subprocess"):
                found.append(f"asyncio.{node.attr}")
    return found


TLS_SAMPLES = (
    "requests.get(u, verify=False)",
    "ctx.verify_mode = ssl.CERT_NONE",
    "ctx.check_hostname = False",
    "PoolManager(assert_hostname=False)",
    "ssl._create_unverified_context()",
    "from ssl import CERT_NONE",
)


def _is_false(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def tls_violations(source: str) -> list[str]:
    """Code (not text in docstrings) that would weaken certificate or host-name verification."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg in {"verify", "assert_hostname", "check_hostname"}:
            if _is_false(node.value):
                found.append(f"{node.arg}=False")
        elif isinstance(node, ast.Assign) and _is_false(node.value):
            found += [
                f"{t.attr} = False"
                for t in node.targets
                if isinstance(t, ast.Attribute) and t.attr in {"check_hostname", "verify"}
            ]
        elif isinstance(node, ast.Attribute) and node.attr in {"CERT_NONE", "_create_unverified_context"}:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id == "CERT_NONE":
            found.append("CERT_NONE")
        elif isinstance(node, ast.ImportFrom) and node.module == "ssl":
            found += [a.name for a in node.names if a.name in {"CERT_NONE", "_create_unverified_context"}]
    return found


# -- 1. no host execution -------------------------------------------------------------------------


def test_no_platform_module_can_start_a_process() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): hits
        for path in platform_modules()
        if (hits := process_violations(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\n",
        "from subprocess import run\n",
        "import os\nos.system('x')\n",
        "import os\nos.execvp('x', [])\n",
        "from os import spawnl\n",
        "import os\nos.startfile('x')\n",
        "import asyncio\nasyncio.create_subprocess_exec('x')\n",
        "import pty\n",
    ],
)
def test_process_scan_negative_control(code: str) -> None:
    assert process_violations(code), code


def test_process_scan_ignores_harmless_code() -> None:
    assert process_violations("import os\nos.environ.get('X')\nfrom os import path\n") == []


# -- 2. TLS verification never disabled ------------------------------------------------------------


def test_no_platform_module_disables_tls_verification() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): hits
        for path in platform_modules()
        if (hits := tls_violations(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


@pytest.mark.parametrize("code", TLS_SAMPLES)
def test_tls_scan_negative_control(code: str) -> None:
    assert tls_violations(code), code


def test_tls_scan_ignores_documentation() -> None:
    assert tls_violations('"""The adapter refuses verify=False and CERT_NONE."""\nx = 1\n') == []


# -- 3. import boundaries -----------------------------------------------------------------------------


def test_sandbox_contracts_kept_on_the_real_repository() -> None:
    result = _lint_imports(None)
    assert result.returncode == 0, result.stdout + result.stderr
    flat = " ".join(result.stdout.split())
    for contract in (DOCKER, ENGINE):
        assert f"{contract} KEPT" in flat, contract


@pytest.mark.parametrize(
    ("package", "code", "contract"),
    [
        ("devteam_orchestration", "import docker\n", DOCKER),
        ("devteam_services", "import docker\n", DOCKER),
        ("devteam_harness", "import requests\n", DOCKER),
        ("devteam_api", "import docker\n", DOCKER),
        ("devteam_worker", "from devteam_harness.sandbox.engine import connect\n", ENGINE),
        ("devteam_api", "from devteam_harness.sandbox import tls\n", ENGINE),
        ("devteam_orchestration", "import devteam_harness.sandbox.engine\n", ENGINE),
    ],
)
def test_each_planted_sandbox_violation_breaks_its_contract(
    tmp_path: Path, package: str, code: str, contract: str
) -> None:
    result = _lint_imports(_planted_copy(tmp_path, package, code))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{contract} BROKEN" in " ".join(result.stdout.split()), result.stdout


def test_ruff_bans_docker_and_sandbox_in_agents(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    planted = root / "agents" / "tester" / "src" / "devteam_agents" / "tester" / "p.py"
    planted.write_text(
        "import docker\nfrom devteam_harness.sandbox import engine\n", encoding="utf-8", newline="\n"
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
    for banned in ("docker", "devteam_harness.sandbox"):
        assert f"`{banned}` is banned" in result.stdout, (banned, result.stdout)
