"""Part E.1: configuration refusals (D-50), contract honesty rules, engine identity checks (no Docker)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from devteam_core.config.settings import SandboxSettings
from devteam_core.contracts.sandbox import (
    ContainerLimits,
    RecipeOutcome,
    RecipeSpec,
    SandboxExecution,
    SandboxExecutionStatus,
    TemplateDependencies,
    TemplateVerification,
    TemplateVerificationStatus,
    TestCounts,
)
from devteam_core.ids import new_id
from devteam_harness.sandbox.engine import probe
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.tls import SandboxTLSError, build_client_context, check_key_file
from devteam_harness.secrets import SecretsManager
from devteam_harness.secrets.backends import MemoryBackend

pytestmark = pytest.mark.security
T0 = datetime(2026, 11, 23, 9, tzinfo=UTC)
LIMITS = ContainerLimits(
    memory_mb=1536, cpus=1.5, pids=256, wall_clock_s=600, tmpfs_mb=256, work_copy_mb=512, output_kb=1024
)


# -- D-50: only the loopback mutual-TLS endpoint ------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "npipe:////./pipe/dockerDesktopLinuxEngine",
        "npipe:////./pipe/docker_engine",
        "unix:///var/run/docker.sock",
        "tcp://0.0.0.0:2376",
        "tcp://192.168.1.10:2376",
        "tcp://localhost:2376",
        "tcp://127.0.0.1",
        "ssh://user@127.0.0.1",
        "http://127.0.0.1:2375",
        "https://127.0.0.1:2376",
        "tcp://user:pw@127.0.0.1:2376",
    ],
)
def test_docker_host_other_than_loopback_tls_is_refused(host: str) -> None:
    with pytest.raises(ValidationError):
        SandboxSettings(docker_host=host)


def test_docker_host_default_is_loopback_tls() -> None:
    assert SandboxSettings().docker_host == "tcp://127.0.0.1:2376"


def test_unknown_sandbox_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SandboxSettings.model_validate({"network": "bridge"})  # there is no network setting at all


# -- TLS material -------------------------------------------------------------------------------


def test_plaintext_client_key_is_refused(tmp_path: Path) -> None:
    key = tmp_path / "client-key.enc.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n", encoding="ascii")
    with pytest.raises(SandboxTLSError):
        check_key_file(key)
    key.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n", encoding="ascii")
    with pytest.raises(SandboxTLSError):
        check_key_file(key)


def test_missing_tls_files_block(tmp_path: Path) -> None:
    with pytest.raises(SandboxTLSError) as info:
        build_client_context(tmp_path, SecretsManager(MemoryBackend({})))
    assert info.value.code == "sandbox.tls_not_configured"
    assert isinstance(info.value, SandboxBlockedError)


# -- engine identity ----------------------------------------------------------------------------


class _Api:
    def __init__(self, version: dict[str, Any], info: dict[str, Any]) -> None:
        self._version, self._info = version, info

    def version(self, api_version: bool = True) -> dict[str, Any]:
        return self._version

    def info(self) -> dict[str, Any]:
        return self._info


class _Client:
    def __init__(self, version: dict[str, Any], info: dict[str, Any]) -> None:
        self.api = _Api(version, info)


GOOD_VERSION = {
    "Version": "29.1.3",
    "ApiVersion": "1.52",
    "MinAPIVersion": "1.44",
    "Os": "linux",
    "Platform": {"Name": ""},
}


def test_wsl_engine_is_accepted() -> None:
    info = probe(_Client(GOOD_VERSION, {"OperatingSystem": "Ubuntu 24.04.4 LTS"}))
    assert info.version == "29.1.3"


@pytest.mark.parametrize(
    ("version", "info"),
    [
        (GOOD_VERSION, {"OperatingSystem": "Docker Desktop"}),
        ({**GOOD_VERSION, "Platform": {"Name": "Docker Desktop 4.50.0"}}, {"OperatingSystem": "Ubuntu"}),
        ({**GOOD_VERSION, "Os": "windows"}, {"OperatingSystem": "Windows Server"}),
        ({**GOOD_VERSION, "MinAPIVersion": "1.45"}, {"OperatingSystem": "Ubuntu"}),
        ({**GOOD_VERSION, "ApiVersion": "1.40"}, {"OperatingSystem": "Ubuntu"}),
    ],
)
def test_desktop_windows_or_incompatible_engines_are_blocked(
    version: dict[str, Any], info: dict[str, Any]
) -> None:
    with pytest.raises(SandboxBlockedError) as caught:
        probe(_Client(version, info))
    assert caught.value.code == "sandbox.engine_not_allowed"


# -- contract honesty -----------------------------------------------------------------------------


def _execution(**kw: Any) -> SandboxExecution:
    base: dict[str, Any] = {
        "execution_id": new_id("sbx"),
        "project_id": new_id("prj"),
        "recipe": "pytest_api",
        "image": "devteam-sandbox/python_api",
        "limits": LIMITS,
        "status": "completed",
        "exit_code": 0,
        "duration_ms": 5,
        "started_at": T0,
        "finished_at": T0 + timedelta(seconds=1),
    }
    return SandboxExecution.model_validate({**base, **kw})


@pytest.mark.parametrize(
    "kw",
    [
        {"status": "completed", "exit_code": 1},
        {"status": "completed", "exit_code": None},
        {"status": "failed", "exit_code": 0, "reason": "x"},
        {"status": "blocked", "exit_code": 0, "reason": "engine down"},
        {
            "status": "blocked",
            "exit_code": None,
            "reason": "x",
            "tests": {"tests": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0},
        },
        {"status": "timed_out", "exit_code": None},  # no reason
        {"verification_id": None, "project_id": None},
    ],
)
def test_dishonest_executions_are_rejected(kw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _execution(**kw)


def test_honest_executions_are_accepted() -> None:
    assert _execution().status is SandboxExecutionStatus.COMPLETED
    assert _execution(status="blocked", exit_code=None, reason="engine down").exit_code is None


def _outcome(**kw: Any) -> RecipeOutcome:
    return RecipeOutcome.model_validate({"recipe": "pytest_api", "status": "completed", "exit_code": 0, **kw})


@pytest.mark.parametrize(
    "outcome",
    [
        {"reports_tests": True, "tests": None},  # tests never ran
        {"reports_tests": True, "tests": {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}},
        {"reports_tests": True, "tests": {"tests": 2, "passed": 0, "failed": 0, "errors": 0, "skipped": 2}},
        {"reports_tests": True, "tests": {"tests": 2, "passed": 1, "failed": 1, "errors": 0, "skipped": 0}},
        {"status": "failed", "exit_code": 2},
        {"status": "blocked", "exit_code": None, "reason": "x"},
    ],
)
def test_verification_cannot_pass_without_executed_clean_tests(outcome: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TemplateVerification(
            verification_id=new_id("tvf"),
            template_id="react_fastapi_sqlite",
            template_version="1.0.0",
            template_checksum="a" * 64,
            status=TemplateVerificationStatus.PASSED,
            recipes=(_outcome(**outcome),),
            image_digests={"python_api": "sha256:" + "b" * 64},
            requested_by="owner",
            requested_at=T0,
            finished_at=T0,
        )


def test_verification_passes_only_with_real_results() -> None:
    passed = TemplateVerification(
        verification_id=new_id("tvf"),
        template_id="react_fastapi_sqlite",
        template_version="1.0.0",
        template_checksum="a" * 64,
        status=TemplateVerificationStatus.PASSED,
        recipes=(
            _outcome(
                reports_tests=True, tests={"tests": 3, "passed": 3, "failed": 0, "errors": 0, "skipped": 0}
            ),
            _outcome(recipe="npm_build"),
        ),
        image_digests={"python_api": "sha256:" + "b" * 64},
        requested_by="owner",
        requested_at=T0,
        finished_at=T0,
    )
    assert passed.status is TemplateVerificationStatus.PASSED


def test_test_counts_must_add_up() -> None:
    with pytest.raises(ValidationError):
        TestCounts(tests=3, passed=1, failed=0, errors=0, skipped=0)


@pytest.mark.parametrize(
    "argv", [("sh", "-c", "x"), ("npm", "run", "build"), ("npx", "vite"), ("pip", "install", "x")]
)
def test_recipes_never_run_a_shell_or_package_manager(argv: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        RecipeSpec(
            recipe="bad_recipe",
            image="node_web",
            argv=argv,
            workdir="/work",
            env={},
            timeout_s=10,
            tool="request_build",
        )


@pytest.mark.parametrize("dep", ["react", "react>=18", "react==", "React==18.3.1", "../x==1.0"])
def test_dependencies_must_be_pinned(dep: str) -> None:
    with pytest.raises(ValidationError):
        TemplateDependencies(node=(dep,))
