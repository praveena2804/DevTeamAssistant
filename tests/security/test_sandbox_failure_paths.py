"""Part E failure paths without Docker: untrusted test reports, engine errors, evidence paths.

Every branch here is a fail-closed or refuse path. The real engine proves the main paths in ``tests/sandbox``;
these tests make sure the error branches behave as documented (they are hard to trigger on a healthy engine).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docker import errors  # tests only: the SDK's exception types for the fake client

from devteam_core.contracts.sandbox import SandboxExecutionStatus
from devteam_harness.sandbox.engine import Engine, EngineInfo, PinnedTLSAdapter, https_url, probe
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.results import MAX_CASES, ReportError, parse_coverage, parse_junit
from devteam_harness.sandbox.runner import RunResult
from devteam_harness.sandbox.service import Evidence, SandboxService, _reports, _write_evidence
from devteam_harness.sandbox.spec import ContainerSpec
from devteam_harness.sandbox.tls import SandboxTLSError
from tests.security.test_sandbox_units import CEILING

pytestmark = pytest.mark.security
NOW = datetime(2026, 11, 24, tzinfo=UTC)
INFO = EngineInfo("29.1.3", "1.52", "1.24", "linux", "Ubuntu 24.04.4 LTS", "Docker Engine - Community")


# -- untrusted JUnit / coverage reports ------------------------------------------------------------------


def _junit(cases: str, root: str = "testsuite") -> bytes:
    return f"<{root}>{cases}</{root}>".encode()


def test_counts_come_from_the_cases_not_from_attributes() -> None:
    report = parse_junit(
        b'<testsuite tests="999" failures="0">'
        b'<testcase classname="a" name="ok"><properties><property name="req" value="FR-001"/>'
        b'<property name="req" value="DROP TABLE"/></properties></testcase>'
        b'<testcase name="bad"><failure/></testcase>'
        b'<testcase name="boom"><error/></testcase>'
        b'<testcase name="later"><skipped/></testcase>'
        b"</testsuite>"
    )
    counts = report.counts
    assert (counts.tests, counts.passed, counts.failed, counts.errors, counts.skipped) == (4, 1, 1, 1, 1)
    assert report.cases[0].requirements == ("FR-001",)  # malformed requirement markers are dropped
    assert not counts.clean


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<!DOCTYPE x [<!ENTITY a 'b'>]><testsuite/>", "DTD or entity"),
        (b"<testsuite><testcase", "not valid XML"),
        (b"<html><testcase name='x'/></html>", "not a JUnit report"),
    ],
)
def test_hostile_or_foreign_reports_are_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ReportError, match=message):
        parse_junit(data)


def test_oversized_report_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("devteam_harness.sandbox.results.MAX_REPORT_BYTES", 10)
    with pytest.raises(ReportError, match="too large"):
        parse_junit(_junit('<testcase name="x"/>'))


def test_case_count_is_capped() -> None:
    report = parse_junit(_junit('<testcase name="x"/>' * (MAX_CASES + 3), root="testsuites"))
    assert report.counts.tests == MAX_CASES


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b'<coverage line-rate="0.873"/>', 87.3),
        (b"<coverage/>", None),
        (b'<coverage line-rate="lots"/>', None),
        (b"<!DOCTYPE x><coverage line-rate='1'/>", None),
        (b"not xml", None),
    ],
)
def test_coverage_is_read_defensively(data: bytes, expected: float | None) -> None:
    assert parse_coverage(data) == expected


# -- engine error paths (fake SDK client) ----------------------------------------------------------------


class _Api:
    """Fake low-level API: every call raises ``fail`` when set, otherwise returns harmless values."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.removed: list[str] = []

    def _maybe_fail(self) -> None:
        if self.fail is not None:
            raise self.fail

    def inspect_image(self, _tag: str) -> dict[str, str]:
        self._maybe_fail()
        return {"Id": "sha256:abc"}

    def create_volume(self, **_kw: Any) -> None:
        self._maybe_fail()

    def remove_container(self, container_id: str, **_kw: Any) -> None:
        self.removed.append(container_id)
        self._maybe_fail()

    def remove_volume(self, name: str, **_kw: Any) -> None:
        self.removed.append(name)
        self._maybe_fail()

    def put_archive(self, *_a: Any) -> bool:
        return False

    def stats(self, *_a: Any, **_kw: Any) -> Any:
        self._maybe_fail()
        return iter([{"memory_stats": {}}])

    def version(self, **_kw: Any) -> dict[str, Any]:
        self._maybe_fail()
        return {"Version": "29.1.3", "ApiVersion": "1.x", "MinAPIVersion": "1.24", "Os": "linux"}

    def info(self) -> dict[str, str]:
        return {"OperatingSystem": "Ubuntu 24.04.4 LTS"}


class _Client:
    def __init__(self, api: _Api) -> None:
        self.api = api

    def close(self) -> None:
        pass


def _engine(api: _Api) -> Engine:
    return Engine(_Client(api), None, INFO)  # type: ignore[arg-type]


def _spec() -> ContainerSpec:
    return ContainerSpec(
        image="devteam-sandbox/python_api:1", argv=("python", "-c", "pass"), workdir="/work", env={},
        limits=CEILING, execution_id="sbx_01JD3X5Q0000000000000000AA",
    )  # fmt: skip


def test_unreachable_engine_blocks_image_lookup_and_create_cleans_up() -> None:
    api = _Api(OSError("connection refused"))
    engine = _engine(api)
    with pytest.raises(SandboxBlockedError) as lookup:
        engine.image_id("devteam-sandbox/python_api:1")
    assert lookup.value.code == "sandbox.docker_unavailable"
    with pytest.raises(SandboxBlockedError):
        engine.create(_spec())
    assert api.removed == [_spec().volume_name]  # the half-created volume is removed again


def test_engine_refusal_is_reported_as_engine_error() -> None:
    engine = _engine(_Api(errors.APIError("refused")))
    with pytest.raises(SandboxBlockedError) as info:
        engine.image_id("x")
    assert info.value.code == "sandbox.engine_error"


def test_missing_image_is_none_and_copy_in_failure_blocks() -> None:
    assert _engine(_Api(errors.ImageNotFound("gone"))).image_id("x") is None
    with pytest.raises(SandboxBlockedError) as info:
        _engine(_Api()).put_tar("c1", "/work", b"")
    assert info.value.code == "sandbox.copy_in_failed"


@pytest.mark.parametrize("failure", [errors.NotFound("gone"), OSError("down")])
def test_remove_never_raises(failure: BaseException) -> None:
    api = _Api(failure)
    _engine(api).remove("c1", "v1")
    assert api.removed == ["c1", "v1"]


def test_broken_stats_stream_ends_quietly() -> None:
    assert list(_engine(_Api(ValueError("bad json"))).stats_stream("c1")) == []


def test_unparseable_api_version_is_refused() -> None:
    with pytest.raises(SandboxBlockedError) as info:
        probe(_Client(_Api()))
    assert info.value.code == "sandbox.engine_not_allowed"


@pytest.mark.parametrize("host", ["tcp://localhost:2376", "unix:///var/run/docker.sock", "tcp://127.0.0.1"])
def test_only_loopback_tcp_with_a_port_is_accepted(host: str) -> None:
    with pytest.raises(SandboxTLSError):
        https_url(host)


def test_tls_verification_cannot_be_switched_off() -> None:
    adapter = PinnedTLSAdapter.__new__(PinnedTLSAdapter)  # no pool needed for this check
    with pytest.raises(SandboxTLSError):
        adapter.cert_verify(object(), "https://127.0.0.1:2376", False, None)


# -- execution service helpers ---------------------------------------------------------------------------


def _result(
    outputs: dict[str, bytes], status: SandboxExecutionStatus = SandboxExecutionStatus.COMPLETED
) -> RunResult:
    return RunResult(
        status=status, exit_code=0, duration_ms=5, peak_memory_mb=10.0, stdout=b"out", stderr=b"",
        outputs=outputs, reason=None, image_id="sha256:abc", engine_version="29.1.3",
        started_at=NOW, finished_at=NOW,
    )  # fmt: skip


def test_a_test_recipe_without_a_report_is_never_counted() -> None:
    report, coverage, reason = _reports(_result({}), reports_tests=True)
    assert report is None and coverage is None and reason == "no test report was produced"
    report, _, reason = _reports(_result({"junit.xml": b"<!ENTITY x>"}), reports_tests=True)
    assert report is None and reason is not None and reason.startswith("test report rejected")


def test_evidence_never_leaves_its_folder(tmp_path: Path) -> None:
    result = _result(
        {"junit.xml": b"<testsuite/>", "../escape.txt": b"x", "/abs.txt": b"y", "a/../../b": b"z"}
    )
    paths = _write_evidence(Evidence(root=tmp_path, prefix="sandbox/x"), "sbx_1", result)
    assert sorted(paths) == [
        "sandbox/x/sbx_1/out/junit.xml",
        "sandbox/x/sbx_1/stderr.txt",
        "sandbox/x/sbx_1/stdout.txt",
    ]
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "sandbox" / "b").exists()


class _Unreachable(AbstractContextManager[Engine]):
    def __enter__(self) -> Engine:
        raise SandboxBlockedError("sandbox.docker_unavailable", "the Docker Engine in WSL is not reachable")

    def __exit__(self, *_exc: object) -> None:
        return None


class _Healthy(AbstractContextManager[Engine]):
    def __enter__(self) -> Engine:
        engine = _engine(_Api())
        engine.sweep = lambda: (2, 1)  # type: ignore[method-assign]
        return engine

    def __exit__(self, *_exc: object) -> None:
        return None


def _service(factory: Any, tmp_path: Path) -> SandboxService:
    from devteam_core.config import load_settings  # noqa: PLC0415
    from tests.fixtures.harness import REPO_ROOT  # noqa: PLC0415

    settings = load_settings(root=REPO_ROOT, profile="test", environ={})
    return SandboxService(settings, None, None, engine_factory=factory)  # type: ignore[arg-type]


def test_status_and_sweep_report_an_unreachable_engine_honestly(tmp_path: Path) -> None:
    service = _service(_Unreachable, tmp_path)
    assert service.engine_status() == {
        "reachable": False,
        "reason": "sandbox.docker_unavailable: the Docker Engine in WSL is not reachable",
    }
    assert service.sweep() is None


def test_status_and_sweep_on_a_healthy_engine(tmp_path: Path) -> None:
    service = _service(_Healthy, tmp_path)
    assert service.engine_status() == {
        "reachable": True,
        "version": "29.1.3",
        "operating_system": "Ubuntu 24.04.4 LTS",
    }
    assert service.sweep() == (2, 1)


def test_a_disabled_sandbox_never_connects() -> None:
    from devteam_core.config import load_settings  # noqa: PLC0415
    from devteam_harness.sandbox.engine import connect  # noqa: PLC0415
    from tests.fixtures.harness import REPO_ROOT  # noqa: PLC0415

    settings = load_settings(root=REPO_ROOT, profile="test", environ={})
    disabled = settings.model_copy(update={"sandbox": settings.sandbox.model_copy(update={"enabled": False})})
    with pytest.raises(SandboxBlockedError) as info:
        connect(disabled, None)  # type: ignore[arg-type]
    assert info.value.code == "sandbox.disabled"
