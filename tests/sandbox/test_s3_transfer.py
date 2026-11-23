"""Spike S-3, kept as permanent tests: file transfer and WSL behaviour (Part E plan §3.12; D-48)."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from devteam_core.config import Settings
from devteam_core.ids import new_id
from devteam_harness.sandbox.engine import Engine, connect
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.runner import SandboxRunner, build_tar
from devteam_harness.sandbox.spec import ContainerSpec
from devteam_harness.secrets import SecretsManager
from tests.sandbox.conftest import limits, python_probe

pytestmark = [pytest.mark.docker, pytest.mark.security]

STAT = """
import os, json
rows = {}
for root, dirs, files in os.walk('/work/app'):
    for name in dirs + files:
        st = os.stat(os.path.join(root, name))
        key = os.path.relpath(os.path.join(root, name), '/work')
        rows[key] = [st.st_uid, st.st_gid, oct(st.st_mode & 0o777)]
print('STAT ' + json.dumps(rows, sort_keys=True))
os.makedirs('/work/.devteam/out/reports', exist_ok=True)
open('/work/.devteam/out/reports/result.txt', 'w').write('collected')
open('/work/not-collected.txt', 'w').write('stays inside')
"""


def test_copy_in_ownership_and_copy_out_scope(runner: SandboxRunner) -> None:
    files = {"app/main.py": b"print('x')\n", "app/pkg/__init__.py": b"", "app/pkg/data.json": b"{}"}
    result = runner.run(python_probe(STAT, files=files))
    line = next(row for row in result.stdout.decode().splitlines() if row.startswith("STAT "))
    import json  # noqa: PLC0415

    rows = json.loads(line[5:])
    assert rows["app/main.py"] == [10001, 10001, "0o644"]
    assert rows["app/pkg"] == [10001, 10001, "0o755"]
    assert rows["app/pkg/data.json"][0] == 10001
    assert result.outputs == {"reports/result.txt": b"collected"}  # only the output folder comes back


def test_copy_in_works_on_a_read_only_container_with_only_the_volume_mounted(engine: Engine) -> None:
    spec = ContainerSpec(
        image="devteam-sandbox/python_api:1",
        argv=("python", "-c", "print(open('/work/a.txt').read())"),
        workdir="/work",
        env={},
        limits=limits(),
        execution_id=new_id("sbx"),
    )
    container = engine.create(spec)
    try:
        record = engine.inspect(container)
        assert record["HostConfig"]["ReadonlyRootfs"] is True
        mounts = record["Mounts"]
        assert [(m["Type"], m["Destination"]) for m in mounts] == [("volume", "/work")]
        assert not record["HostConfig"].get("Binds")
        engine.put_tar(container, "/work", build_tar({"a.txt": b"copied-in"}, 1024))
        engine.start(container)
        assert engine.wait(container, 60) == 0
        assert b"copied-in" in b"".join(engine.log_chunks(container, stdout=True))
    finally:
        engine.remove(container, spec.volume_name)
    containers, volumes = engine.labelled()
    assert container not in containers and spec.volume_name not in volumes


@pytest.mark.slow
def test_engine_stays_reachable_with_keepalive_and_is_blocked_after_wsl_shutdown(
    live_settings: Settings, live_secrets: SecretsManager, wsl_keepalive: None
) -> None:
    """S-3 #5. Duration from DEVTEAM_S3_IDLE_MINUTES (default 30). Ends with ``wsl --shutdown``: run last."""
    minutes = float(os.environ.get("DEVTEAM_S3_IDLE_MINUTES", "30"))
    deadline = time.monotonic() + minutes * 60
    checks = 0
    while time.monotonic() < deadline:
        with connect(live_settings, live_secrets, timeout=15) as engine:
            assert engine.client.ping()
        checks += 1
        time.sleep(min(60.0, max(deadline - time.monotonic(), 0)))
    assert checks >= max(1, int(minutes) - 1)
    subprocess.run(["wsl.exe", "--shutdown"], check=True, timeout=120)
    time.sleep(5)
    with pytest.raises(SandboxBlockedError) as info:
        connect(live_settings, live_secrets, timeout=5)
    assert info.value.code == "sandbox.docker_unavailable"
