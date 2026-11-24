"""Isolation suite (AC-E-02; Part E plan §7; TS-11). Real WSL engine, real containers, no mocks.

Each control is probed through the production path (:class:`SandboxRunner`). Each probe also has a
**negative control**: the same probe in a container with only that one control removed
(:func:`run_raw`) behaves differently, which proves the probe really detects the control.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from devteam_core.contracts.sandbox import SandboxExecutionStatus
from devteam_core.ids import new_id
from devteam_harness.sandbox.engine import Engine
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.images import ImageLock, LockedImage
from devteam_harness.sandbox.runner import RunResult, SandboxRunner
from devteam_harness.sandbox.spec import ContainerSpec, locked_down_problems
from tests.sandbox.conftest import limits, python_probe, run_raw

pytestmark = [pytest.mark.docker, pytest.mark.security]

PY = "devteam-sandbox/python_api:1"


def _out(result: RunResult) -> str:
    return (result.stdout + result.stderr).decode("utf-8", "replace")


def _leftovers(engine: Engine, execution_id: str) -> list[str]:
    containers = engine.api.containers(
        all=True, filters={"label": f"devteam.sandbox.execution_id={execution_id}"}
    )
    volumes = (
        engine.api.volumes(filters={"label": f"devteam.sandbox.execution_id={execution_id}"}).get("Volumes")
        or []
    )
    return [c["Id"] for c in containers] + [v["Name"] for v in volumes]


# -- network -------------------------------------------------------------------------------------------

NET_PROBE = """
import os, socket
print('IFACES', sorted(os.listdir('/sys/class/net')))
for target in [('1.1.1.1', 53), ('8.8.8.8', 443)]:
    try:
        socket.create_connection(target, timeout=3); print('TCP-OPEN', target)
    except OSError as e:
        print('TCP-BLOCKED', target, type(e).__name__)
try:
    socket.getaddrinfo('pypi.org', 443); print('DNS-OK')
except OSError as e:
    print('DNS-BLOCKED', type(e).__name__)
"""


def test_no_network_outbound_tcp_and_dns_fail(runner: SandboxRunner) -> None:
    result = runner.run(python_probe(NET_PROBE))
    out = _out(result)
    assert result.status is SandboxExecutionStatus.COMPLETED, out
    assert "IFACES ['lo']" in out
    assert out.count("TCP-BLOCKED") == 2 and "TCP-OPEN" not in out
    assert "DNS-BLOCKED" in out and "DNS-OK" not in out


def test_network_negative_control_bridge_has_an_interface(engine: Engine) -> None:
    code, out = run_raw(
        engine,
        PY,
        "import os;print('IFACES', sorted(os.listdir('/sys/class/net')))",
        host_overrides={"network_mode": "bridge"},
        create_overrides={"network_disabled": False},
    )
    assert code == 0 and "eth0" in out


# -- user, capabilities, privileges ---------------------------------------------------------------------

IDENTITY = """
import os
status = dict(l.split(':', 1) for l in open('/proc/self/status').read().splitlines() if ':' in l)
print('UID', os.getuid(), 'GID', os.getgid())
s = {k: v.strip() for k, v in status.items()}
print('CAPEFF', s['CapEff'], 'CAPBND', s['CapBnd'], 'NNP', s['NoNewPrivs'])
"""


def test_runs_as_non_root_without_capabilities(runner: SandboxRunner) -> None:
    out = _out(runner.run(python_probe(IDENTITY)))
    assert "UID 10001 GID 10001" in out
    assert "CAPEFF 0000000000000000" in out and "CAPBND 0000000000000000" in out
    assert "NNP 1" in out


def test_identity_negative_control_root_with_capabilities(engine: Engine) -> None:
    code, out = run_raw(
        engine,
        PY,
        IDENTITY,
        host_overrides={"cap_drop": None, "security_opt": None},
        create_overrides={"user": "0:0"},
    )
    assert code == 0 and "UID 0" in out and "CAPBND 0000000000000000" not in out and "NNP 0" in out


# -- filesystem ----------------------------------------------------------------------------------------

FS_PROBE = """
import os
for path in ['/var/tmp/x', '/usr/local/x', '/node_modules_x', '/etc/x']:
    try:
        open(path, 'w').write('x'); print('WROTE', path)
    except OSError as e:
        print('REFUSED', path, e.errno)
open('/work/ok.txt', 'w').write('fine'); print('WORK-OK')
open('/tmp/ok.txt', 'w').write('fine'); print('TMP-OK')
mounts = open('/proc/mounts').read()
print('HOSTFS', any(fs in mounts for fs in (' 9p ', ' drvfs ', ' virtiofs ', 'docker.sock')))
print('MNT-C', os.path.exists('/mnt/c'))
"""


def test_root_filesystem_is_read_only_and_no_host_paths(runner: SandboxRunner) -> None:
    out = _out(runner.run(python_probe(FS_PROBE)))
    assert "WROTE" not in out
    assert out.count("REFUSED") == 4
    assert "REFUSED /var/tmp/x 30" in out  # EROFS: refused by the read-only root, not just by permissions
    assert "WORK-OK" in out and "TMP-OK" in out
    assert "HOSTFS False" in out and "MNT-C False" in out


def test_filesystem_negative_control_writable_root(engine: Engine) -> None:
    code, out = run_raw(
        engine, PY, "open('/var/tmp/x','w').write('x');print('WROTE')", host_overrides={"read_only": False}
    )
    assert code == 0 and "WROTE" in out


TMP_EXEC = """
import os, subprocess
open('/tmp/run.sh', 'w').write('#!/bin/sh\\necho EXECUTED\\n'); os.chmod('/tmp/run.sh', 0o755)
try:
    print(subprocess.run(['/tmp/run.sh'], capture_output=True, text=True).stdout.strip())
except PermissionError:
    print('NOEXEC')
"""


def test_tmp_is_not_executable(runner: SandboxRunner) -> None:
    assert "NOEXEC" in _out(runner.run(python_probe(TMP_EXEC)))


def test_tmp_negative_control_exec_allowed(engine: Engine) -> None:
    code, out = run_raw(engine, PY, TMP_EXEC, host_overrides={"tmpfs": {"/tmp": "rw,exec,size=64m"}})
    assert code == 0 and "EXECUTED" in out


# -- environment ---------------------------------------------------------------------------------------


def test_host_environment_never_reaches_the_container(
    runner: SandboxRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "DTCANARY-" + new_id("sbx")
    monkeypatch.setenv("DEVTEAM_SANDBOX_CANARY", canary)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    out = _out(runner.run(python_probe("import os;print(sorted(os.environ.items()))")))
    assert canary not in out and "DEVTEAM_SANDBOX_CANARY" not in out
    assert "proxy.invalid" not in out and "HTTPS_PROXY" not in out
    assert os.environ["DEVTEAM_SANDBOX_CANARY"] == canary  # the canary really was set on the host side


# -- resource limits -----------------------------------------------------------------------------------

MEMORY = "b = bytearray({mb} * 1024 * 1024); b[::4096] = b'x' * len(b[::4096]); print('ALLOCATED')"


def test_memory_limit_kills_the_process(runner: SandboxRunner) -> None:
    result = runner.run(python_probe(MEMORY.format(mb=400), limits=limits(memory_mb=128)))
    assert result.status is SandboxExecutionStatus.OOM_KILLED, _out(result)
    assert "ALLOCATED" not in _out(result)
    assert result.reason and "128 MB" in result.reason


def test_memory_negative_control_enough_memory(runner: SandboxRunner) -> None:
    result = runner.run(python_probe(MEMORY.format(mb=100), limits=limits(memory_mb=512)))
    assert result.status is SandboxExecutionStatus.COMPLETED and "ALLOCATED" in _out(result)
    assert result.peak_memory_mb is not None and result.peak_memory_mb >= 90


FORK_BOMB = """
import threading, time
started = 0
try:
    for _ in range(400):
        threading.Thread(target=time.sleep, args=(5,), daemon=True).start(); started += 1
except RuntimeError:
    pass
print('STARTED', started)
"""


def test_pid_limit_stops_a_fork_bomb(runner: SandboxRunner) -> None:
    result = runner.run(python_probe(FORK_BOMB, limits=limits(pids=48)))
    started = int(_out(result).split("STARTED")[1].split()[0])
    assert started < 48


def test_pid_negative_control_higher_limit(runner: SandboxRunner) -> None:
    result = runner.run(python_probe(FORK_BOMB, limits=limits(pids=1024)))
    assert "STARTED 400" in _out(result)


def test_wall_clock_limit_kills_and_cleans_up(runner: SandboxRunner, engine: Engine) -> None:
    request = python_probe("import time; time.sleep(120)", limits=limits(wall_clock_s=3))
    started = time.monotonic()
    result = runner.run(request)
    assert result.status is SandboxExecutionStatus.TIMED_OUT
    assert result.exit_code is None
    assert time.monotonic() - started < 30
    assert _leftovers(engine, request.execution_id) == []


def test_cancel_stops_the_run_and_cleans_up(runner: SandboxRunner, engine: Engine) -> None:
    cancel = threading.Event()
    request = python_probe("import time; time.sleep(120)", cancel=cancel)
    threading.Timer(2.0, cancel.set).start()
    result = runner.run(request)
    assert result.status is SandboxExecutionStatus.CANCELLED
    assert _leftovers(engine, request.execution_id) == []


# -- cleanup and failure handling ----------------------------------------------------------------------


@pytest.mark.parametrize("code", ["print('ok')", "raise SystemExit(3)"])
def test_container_and_volume_removed_after_success_and_failure(
    runner: SandboxRunner, engine: Engine, code: str
) -> None:
    request = python_probe(code)
    result = runner.run(request)
    assert result.status in {SandboxExecutionStatus.COMPLETED, SandboxExecutionStatus.FAILED}
    if result.status is SandboxExecutionStatus.FAILED:
        assert result.exit_code == 3
    assert _leftovers(engine, request.execution_id) == []


def test_every_run_gets_a_new_container(runner: SandboxRunner) -> None:
    probe = "import os;open('/work/marker','a').write('x');print('SIZE', os.path.getsize('/work/marker'))"
    first, second = runner.run(python_probe(probe)), runner.run(python_probe(probe))
    assert "SIZE 1" in _out(first) and "SIZE 1" in _out(second)  # nothing carried over


def test_startup_sweep_removes_leftovers(engine: Engine) -> None:
    spec = ContainerSpec(
        image=PY,
        argv=("python", "-c", "pass"),
        workdir="/work",
        env={},
        limits=limits(),
        execution_id=new_id("sbx"),
        labels={"devteam.test": "sweep"},
    )
    container = engine.create(spec)  # a "crashed" run: created, never removed
    containers, volumes = engine.labelled()
    assert container in containers and spec.volume_name in volumes
    removed = engine.sweep()
    assert removed[0] >= 1 and removed[1] >= 1
    containers, volumes = engine.labelled()
    assert container not in containers and spec.volume_name not in volumes


def test_image_not_matching_the_lock_is_blocked_before_any_container(engine: Engine) -> None:
    forged = ImageLock({"python_api": LockedImage("python_api", PY, "sha256:" + "0" * 64)})
    request = python_probe("print('should never run')")
    with pytest.raises(SandboxBlockedError) as info:
        SandboxRunner(engine, forged).run(request)
    assert info.value.code == "sandbox.image_digest_mismatch"
    assert _leftovers(engine, request.execution_id) == []


def test_unknown_image_is_blocked(engine: Engine, image_lock: ImageLock) -> None:
    missing = ImageLock(
        {"python_api": LockedImage("python_api", "devteam-sandbox/python_api:999", "sha256:" + "1" * 64)}
    )
    with pytest.raises(SandboxBlockedError) as info:
        SandboxRunner(engine, missing).run(python_probe("print(1)"))
    assert info.value.code == "sandbox.image_missing"


@pytest.mark.parametrize("path", ["../escape.txt", "/abs.txt", "a/../../b.txt", "C:/x.txt", "a\\b.txt"])
def test_file_paths_escaping_work_are_blocked(runner: SandboxRunner, engine: Engine, path: str) -> None:
    request = python_probe("print(1)", files={path: b"x"})
    with pytest.raises(SandboxBlockedError) as info:
        runner.run(request)
    assert info.value.code == "sandbox.bad_path"
    assert _leftovers(engine, request.execution_id) == []


def test_oversized_copy_in_is_blocked(runner: SandboxRunner, engine: Engine) -> None:
    request = python_probe(
        "print(1)", files={"big.bin": b"x" * (2 * 1024 * 1024)}, limits=limits(work_copy_mb=1)
    )
    with pytest.raises(SandboxBlockedError) as info:
        runner.run(request)
    assert info.value.code == "sandbox.copy_in_too_large"
    assert _leftovers(engine, request.execution_id) == []


def test_engine_record_is_checked_after_create(engine: Engine) -> None:
    """The real engine record of a production container passes; any weakened field is reported."""
    spec = ContainerSpec(
        image=PY,
        argv=("python", "-c", "pass"),
        workdir="/work",
        env={},
        limits=limits(),
        execution_id=new_id("sbx"),
    )
    container = engine.create(spec)
    try:
        record = engine.inspect(container)
        assert locked_down_problems(record, spec) == []
        weakened = {
            **record,
            "HostConfig": {**record["HostConfig"], "NetworkMode": "bridge", "Binds": ["/:/host"]},
        }
        problems = locked_down_problems(weakened, spec)
        assert "network is not 'none'" in problems and "host bind mounts present" in problems
    finally:
        engine.remove(container, spec.volume_name)


def test_output_is_capped(runner: SandboxRunner) -> None:
    result = runner.run(python_probe("print('A' * 2_000_000)", limits=limits(output_kb=16)))
    assert len(result.stdout) < 20 * 1024
    assert b"output truncated" in result.stdout


def test_docker_absent_is_blocked(live_settings: object, live_secrets: object) -> None:
    from devteam_harness.sandbox.engine import connect  # noqa: PLC0415

    closed = live_settings.model_copy(  # type: ignore[attr-defined]
        update={"sandbox": live_settings.sandbox.model_copy(update={"docker_host": "tcp://127.0.0.1:2399"})}  # type: ignore[attr-defined]
    )
    with pytest.raises(SandboxBlockedError) as info:
        connect(closed, live_secrets, timeout=5)  # type: ignore[arg-type]
    assert info.value.code == "sandbox.docker_unavailable"
