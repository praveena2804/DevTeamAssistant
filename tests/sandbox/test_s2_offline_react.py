"""Spike S-2, kept as permanent tests: the React starter kit builds offline (Part E plan §3.5).

Every check runs in a container made by the **production** spec builder (read-only root, no network,
UID 10001, no host mounts, all capabilities dropped):

1 the React template type-checks, tests and builds offline;
2 its dependencies come from the image's ``/node_modules`` (standard parent-folder lookup), and no
  ``node_modules`` folder appears under ``/work``;
3 nothing is downloaded: only the loopback interface exists, ``npm``/``npx`` are never run, logs name no
  registry;
4 the trusted image is not modified: same image ID before and after, ``docker diff`` is empty;
5 the restrictions stay effective in the same container (read-only ``/node_modules`` and ``/usr``,
  UID 10001, non-executable ``/tmp``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devteam_core.contracts.sandbox import SandboxExecutionStatus
from devteam_core.ids import new_id
from devteam_harness.sandbox.engine import Engine
from devteam_harness.sandbox.images import ImageLock
from devteam_harness.sandbox.runner import RunRequest, SandboxRunner, build_tar
from devteam_harness.sandbox.spec import ContainerSpec, locked_down_problems
from tests.sandbox.conftest import limits

pytestmark = [pytest.mark.docker, pytest.mark.security]

FRONTEND = (
    Path(__file__).resolve().parents[2] / "templates" / "target_apps" / "react_fastapi_sqlite" / "frontend"
)
VITE = "/node_modules/vite/bin/vite.js"
BUILD = (
    "node",
    VITE,
    "build",
    "--configLoader",
    "runner",
    "--outDir",
    "/work/.devteam/out/dist",
    "--emptyOutDir",
)
TSC = ("node", "/node_modules/typescript/bin/tsc", "--noEmit", "-p", "tsconfig.json")
VITEST = (
    "node",
    "/node_modules/vitest/vitest.mjs",
    "run",
    "--configLoader",
    "runner",
    "--reporter=junit",
    "--outputFile=/work/.devteam/out/junit.xml",
)
ENV = {"CI": "1", "HOME": "/work/.devteam/home"}


def _files() -> dict[str, bytes]:
    return {
        f"frontend/{p.relative_to(FRONTEND).as_posix()}": p.read_bytes()
        for p in FRONTEND.rglob("*")
        if p.is_file()
    }


def _request(argv: tuple[str, ...], env: dict[str, str] | None = None) -> RunRequest:
    return RunRequest(
        image="node_web",
        argv=argv,
        workdir="/work/frontend",
        env={**ENV, **(env or {})},
        files=_files(),
        limits=limits(memory_mb=1536, cpus=1.5, wall_clock_s=600, pids=256),
        execution_id=new_id("sbx"),
    )


def test_template_has_no_vendored_dependencies() -> None:
    assert not (FRONTEND / "node_modules").exists()


def test_typecheck_offline(runner: SandboxRunner) -> None:
    result = runner.run(_request(TSC))
    assert result.status is SandboxExecutionStatus.COMPLETED, (result.stdout + result.stderr).decode()


def test_tests_run_offline_and_really_execute(runner: SandboxRunner) -> None:
    result = runner.run(_request(VITEST, {"NODE_ENV": "test"}))
    assert result.status is SandboxExecutionStatus.COMPLETED, (result.stdout + result.stderr).decode()
    report = result.outputs["junit.xml"].decode()
    assert 'tests="3"' in report and 'failures="0"' in report and 'errors="0"' in report


def test_production_build_offline(runner: SandboxRunner) -> None:
    result = runner.run(_request(BUILD))
    log = (result.stdout + result.stderr).decode()
    assert result.status is SandboxExecutionStatus.COMPLETED, log
    assert "dist/index.html" in result.outputs
    assert any(name.startswith("dist/assets/") and name.endswith(".js") for name in result.outputs)
    assert "registry.npmjs.org" not in log and "npm " not in log
    assert result.peak_memory_mb is not None


PROBE = r"""
const fs = require('fs'), os = require('os'), cp = require('child_process');
const out = {};
const wanted = ['react', 'react-dom', 'vite', '@vitejs/plugin-react'];
out.resolved = wanted.map(m => require.resolve(m, {paths: [process.cwd()]}));
const build = cp.spawnSync(process.execPath, %BUILD%, {encoding: 'utf8'});
out.build_status = build.status;
out.work_node_modules = fs.existsSync('/work/node_modules') || fs.existsSync('/work/frontend/node_modules');
out.interfaces = Object.keys(os.networkInterfaces());
out.uid = process.getuid();
out.writes = {};
for (const p of ['/node_modules/x', '/usr/x', '/node_modules/vite/x']) {
  try { fs.writeFileSync(p, 'x'); out.writes[p] = 'WROTE'; } catch (e) { out.writes[p] = e.code; }
}
fs.writeFileSync('/tmp/t.sh', '#!/bin/sh\necho ran\n', {mode: 0o755});
try { cp.execFileSync('/tmp/t.sh'); out.tmp_exec = 'RAN'; } catch (e) { out.tmp_exec = e.code || 'REFUSED'; }
console.log('PROBE ' + JSON.stringify(out));
""".replace("%BUILD%", json.dumps(list(BUILD[1:])))


def test_dependencies_from_image_no_download_restrictions_hold(runner: SandboxRunner) -> None:
    result = runner.run(_request(("node", "-e", PROBE)))
    line = next(row for row in result.stdout.decode().splitlines() if row.startswith("PROBE "))
    data = json.loads(line[len("PROBE ") :])
    assert data["build_status"] == 0
    assert all(path.startswith("/node_modules/") for path in data["resolved"]), data["resolved"]
    assert data["work_node_modules"] is False
    assert data["interfaces"] == ["lo"]
    assert data["uid"] == 10001
    assert all(code in {"EROFS", "EACCES"} for code in data["writes"].values()), data["writes"]
    assert data["writes"]["/node_modules/x"] == "EROFS"
    assert data["tmp_exec"] != "RAN"


def test_trusted_image_is_not_modified(engine: Engine, image_lock: ImageLock) -> None:
    locked = image_lock.get("node_web")
    before = engine.image_id(locked.tag)
    spec = ContainerSpec(
        image=locked.tag,
        argv=BUILD,
        workdir="/work/frontend",
        env=ENV,
        limits=limits(memory_mb=1536, cpus=1.5, wall_clock_s=600, pids=256),
        execution_id=new_id("sbx"),
    )
    container = engine.create(spec)
    try:
        assert locked_down_problems(engine.inspect(container), spec) == []
        engine.put_tar(container, "/work", build_tar(_files(), 64 * 1024 * 1024))
        engine.start(container)
        assert engine.wait(container, 600) == 0
        # Read-only root: the only differences are the paths the ENGINE creates for every container
        # (the docker-init binary for init=True, the /work mount point and the working folder).
        engine_created = {"/usr", "/usr/sbin", "/usr/sbin/docker-init", "/work", "/work/frontend"}
        changed = {entry["Path"] for entry in engine.diff(container)}
        assert changed <= engine_created, changed - engine_created
        assert not any(path.startswith("/node_modules") for path in changed)
    finally:
        engine.remove(container, spec.volume_name)
    assert engine.image_id(locked.tag) == before == locked.image_id
