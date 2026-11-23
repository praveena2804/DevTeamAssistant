"""Part E.2 unit tests (no Docker): container spec invariants, runner helpers, Resource Guard, licence policy,
image lock. The same controls are proven against the real engine in ``tests/sandbox``.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devteam_core.contracts.sandbox import ContainerLimits, SandboxExecutionStatus
from devteam_core.ids import new_id
from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.guard import ResourceGuard
from devteam_harness.sandbox.images import ImageLock, LockedImage, verify_image
from devteam_harness.sandbox.licences import LicencePolicy, PackageLicence, evaluate
from devteam_harness.sandbox.runner import _capped, _classify, _read_metrics, _read_outputs, build_tar
from devteam_harness.sandbox.spec import (
    LAUNCHER,
    SANDBOX_USER,
    ContainerSpec,
    create_arguments,
    effective_limits,
    host_config_arguments,
)
from devteam_state import create_sqlite_engine, session_factory, upgrade
from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.security

CEILING = ContainerLimits(
    memory_mb=1536, cpus=1.5, pids=256, wall_clock_s=600, tmpfs_mb=256, work_copy_mb=512, output_kb=1024
)
POLICY = LicencePolicy.load(REPO_ROOT / "sandbox" / "images" / "licence_policy.yaml")

limits_strategy = st.builds(
    ContainerLimits,
    memory_mb=st.integers(64, 8192),
    cpus=st.floats(0.1, 8),
    pids=st.integers(16, 4096),
    wall_clock_s=st.integers(1, 3600),
    tmpfs_mb=st.integers(16, 2048),
    work_copy_mb=st.integers(1, 4096),
    output_kb=st.integers(1, 16384),
)


def _spec(limits: ContainerLimits = CEILING) -> ContainerSpec:
    return ContainerSpec(
        image="devteam-sandbox/python_api:1",
        argv=("python", "-c", "pass"),
        workdir="/work",
        env={"CI": "1"},
        limits=limits,
        execution_id=new_id("sbx"),
    )


# -- spec ---------------------------------------------------------------------------------------------


@given(limits_strategy)
def test_requested_limits_never_exceed_the_ceiling(requested: ContainerLimits) -> None:
    effective = effective_limits(CEILING, requested)
    for key in ("memory_mb", "cpus", "pids", "wall_clock_s", "tmpfs_mb", "work_copy_mb", "output_kb"):
        assert getattr(effective, key) <= getattr(CEILING, key)


@given(limits_strategy)
def test_every_container_is_locked_down_whatever_the_limits(limits: ContainerLimits) -> None:
    host, create = host_config_arguments(_spec(limits)), create_arguments(_spec(limits))
    assert host["network_mode"] == "none" and create["network_disabled"] is True
    assert host["read_only"] is True and host["privileged"] is False
    assert host["cap_drop"] == ["ALL"] and "no-new-privileges:true" in host["security_opt"]
    assert host["memswap_limit"] == host["mem_limit"] and host["pids_limit"] == limits.pids
    assert "noexec" in host["tmpfs"]["/tmp"] and set(host["tmpfs"]) == {"/tmp"}
    assert [m["Type"] for m in host["mounts"]] == ["volume"] and "binds" not in host
    assert create["user"] == SANDBOX_USER and create["entrypoint"] == [LAUNCHER]
    assert "devices" not in host and host["ipc_mode"] == "private"


def test_workdir_outside_work_is_refused() -> None:
    spec = ContainerSpec(
        image="x", argv=("python",), workdir="/etc", env={}, limits=CEILING, execution_id=new_id("sbx")
    )
    with pytest.raises(ValueError, match="inside /work"):
        create_arguments(spec)


# -- runner helpers -------------------------------------------------------------------------------------


def test_tar_is_owned_by_the_sandbox_user_and_regular_files_only() -> None:
    with tarfile.open(fileobj=io.BytesIO(build_tar({"a/b/c.txt": b"x", "d.txt": b"y"}, 100))) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert set(members) == {"a", "a/b", "a/b/c.txt", "d.txt"}
    assert all(m.uid == m.gid == 10001 for m in members.values())
    assert all(m.isreg() or m.isdir() for m in members.values())


def _tar_of(entries: list[tarfile.TarInfo], data: dict[str, bytes]) -> list[bytes]:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for info in entries:
            tar.addfile(info, io.BytesIO(data[info.name]) if info.name in data else None)
    return [buffer.getvalue()]


def test_collected_outputs_drop_links_and_escapes() -> None:
    good = tarfile.TarInfo("out/report.xml")
    good.size = 3
    link = tarfile.TarInfo("out/link")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    escape = tarfile.TarInfo("out/../../etc/x")
    escape.size = 1
    outputs = _read_outputs(
        iter(_tar_of([good, link, escape], {"out/report.xml": b"abc", "out/../../etc/x": b"z"})), 1000
    )
    assert outputs == {"report.xml": b"abc"}


def test_output_cap_keeps_head_and_tail() -> None:
    capped = _capped(iter([b"H" * 1000, b"T" * 1000]), 200)
    assert capped.startswith(b"H" * 100) and capped.endswith(b"T" * 100) and b"truncated" in capped


def test_metrics_are_parsed_defensively() -> None:
    def tar(text: bytes) -> list[bytes]:
        info = tarfile.TarInfo("sandbox-metrics.json")
        info.size = len(text)
        return _tar_of([info], {"sandbox-metrics.json": text})

    assert _read_metrics(iter(tar(b'{"peak_bytes": 1048576, "oom_kills": 1}'))) == {
        "peak_bytes": 1048576,
        "oom_kills": 1,
    }
    assert _read_metrics(iter(tar(b'{"peak_bytes": -5, "oom_kills": "x"}'))) == {}
    assert _read_metrics(iter(tar(b"not json"))) == {}
    assert _read_metrics(None) == {}


@pytest.mark.parametrize(
    ("code", "flags", "status"),
    [
        (0, {}, SandboxExecutionStatus.COMPLETED),
        (3, {}, SandboxExecutionStatus.FAILED),
        (137, {"oom": True}, SandboxExecutionStatus.OOM_KILLED),
        (None, {"timed_out": True}, SandboxExecutionStatus.TIMED_OUT),
        (None, {"cancelled": True}, SandboxExecutionStatus.CANCELLED),
        (0, {"oom": True}, SandboxExecutionStatus.OOM_KILLED),  # an OOM kill is never reported as success
    ],
)
def test_classification(code: int | None, flags: dict[str, bool], status: SandboxExecutionStatus) -> None:
    got, reason = _classify(
        code,
        oom=flags.get("oom", False),
        timed_out=flags.get("timed_out", False),
        cancelled=flags.get("cancelled", False),
        limits=CEILING,
    )
    assert got is status
    assert (reason is None) == (status is SandboxExecutionStatus.COMPLETED)


# -- Resource Guard ----------------------------------------------------------------------------------------


class _Models:
    """Model-residency port (which models Ollama holds in memory). Not an AI provider: no answers."""

    def __init__(self, loaded: tuple[str, ...], *, releases: bool = True) -> None:
        self.loaded, self.releases, self.unloaded = list(loaded), releases, []  # type: ignore[var-annotated]

    def loaded_models(self) -> tuple[str, ...]:
        return tuple(self.loaded)

    def unload(self, model: str) -> None:
        self.unloaded.append(model)
        if self.releases:
            self.loaded.remove(model)


@pytest.fixture
def lease(tmp_path: Path) -> ResourceLease:
    db = tmp_path / "state.db"
    upgrade(db)
    return ResourceLease(session_factory(create_sqlite_engine(db)), ttl_s=5, wait_s=0.3, poll_s=0.05)


def _guard(lease: ResourceLease, models: _Models | None, free: int = 8000, **kw: object) -> ResourceGuard:
    return ResourceGuard(
        lease,
        min_free_ram_mb=2048,
        models=models,
        free_ram=lambda: free,
        settle_s=0,
        unload_timeout_s=0.2,
        sleep=lambda _s: None,
        **kw,  # type: ignore[arg-type]
    )


def test_guard_unloads_models_then_holds_the_lease(lease: ResourceLease) -> None:
    models = _Models(("qwen2.5:3b",))
    with _guard(lease, models).acquire():
        assert models.unloaded == ["qwen2.5:3b"] and lease.holder_now() == "sandbox"
    assert lease.holder_now() is None


def test_guard_waits_for_resources_when_memory_is_low(lease: ResourceLease) -> None:
    with pytest.raises(ResourceUnavailableError) as info:
        _guard(lease, _Models(()), free=1000).acquire()
    assert info.value.code == "resource.low_memory" and lease.holder_now() is None


def test_guard_refuses_when_a_model_stays_loaded(lease: ResourceLease) -> None:
    with pytest.raises(ResourceUnavailableError) as info:
        _guard(lease, _Models(("m",), releases=False)).acquire()
    assert info.value.code == "resource.model_loaded" and lease.holder_now() is None
    with pytest.raises(ResourceUnavailableError):
        _guard(lease, _Models(("m",)), unload_models=False).acquire()


def test_guard_waits_while_an_llm_call_holds_the_lease(lease: ResourceLease) -> None:
    with lease.acquire("llm"), pytest.raises(ResourceUnavailableError) as info:
        _guard(lease, _Models(())).acquire()
    assert info.value.code == "resource.busy"


# -- licence policy ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "ok"),
    [
        (PackageLicence("python", "fastapi", "1", "MIT", 4), True),
        (PackageLicence("node", "react", "1", "MIT", 4), True),
        (PackageLicence("python", "x", "1", "MIT OR GPL-3.0-only", 3), True),
        (PackageLicence("python", "x", "1", "MIT AND GPL-3.0-only", 3), False),
        (PackageLicence("python", "x", "1", "GPL-3.0-only", 3), False),
        (PackageLicence("python", "x", "1", "AGPL-3.0", 3), False),
        (PackageLicence("python", "x", "1", "", 3), False),  # unknown licence
        (PackageLicence("python", "x", "1", "Some Custom Licence", 3), False),
        (PackageLicence("python", "unlisted", "1", "MPL-2.0", 3), False),  # MPL only when reviewed
        (PackageLicence("python", "certifi", "1", "Mozilla Public License 2.0 (MPL 2.0)", 3), True),
        (PackageLicence("python", "certifi", "1", "MPL-2.0", 4), False),  # tier 4: permissive only
        (PackageLicence("node", "x", "1", "(MIT OR CC0-1.0)", 3), True),
        (PackageLicence("debian", "bash", "5", "GPL-3+", 1), True),  # OS layer: recorded, allowed
        (PackageLicence("node", "x", "1", "UNLICENSED", 3), False),
    ],
)
def test_licence_policy(row: PackageLicence, ok: bool) -> None:
    assert (evaluate([row], POLICY) == []) is ok


# -- image lock ----------------------------------------------------------------------------------------------


def test_image_lock_errors_block(tmp_path: Path) -> None:
    with pytest.raises(SandboxBlockedError):
        ImageLock.load(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text(json.dumps({"schema": 99}), encoding="utf-8")
    with pytest.raises(SandboxBlockedError):
        ImageLock.load(tmp_path / "bad.json")
    lock = ImageLock({"python_api": LockedImage("python_api", "t:1", "sha256:" + "a" * 64)})
    with pytest.raises(SandboxBlockedError):
        lock.get("node_web")
    with pytest.raises(SandboxBlockedError):
        verify_image(lambda _tag: None, lock.get("python_api"))
    with pytest.raises(SandboxBlockedError):
        verify_image(lambda _tag: "sha256:" + "b" * 64, lock.get("python_api"))
    assert verify_image(lambda _tag: "sha256:" + "a" * 64, lock.get("python_api")).endswith("a")
