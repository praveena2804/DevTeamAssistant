"""Fixtures for tests that talk to the real Docker Engine in WSL2 (marker ``docker``).

These tests never mock the engine. If the engine is not reachable they **fail** (they are only selected
when a Docker run is intended), so an unexecuted check can never look like a pass.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from devteam_core.config import Settings, load_settings
from devteam_core.contracts.sandbox import ContainerLimits
from devteam_core.ids import new_id
from devteam_harness.sandbox.engine import Engine, connect
from devteam_harness.sandbox.images import ImageLock
from devteam_harness.sandbox.runner import RunRequest, SandboxRunner
from devteam_harness.sandbox.spec import ContainerSpec, create_arguments, host_config_arguments
from devteam_harness.secrets import SecretsManager, secrets_manager_from_settings


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    """The real configuration (default profile): real TLS files and the real Credential Manager entry."""
    return load_settings()


@pytest.fixture(scope="session")
def live_secrets(live_settings: Settings) -> SecretsManager:
    return secrets_manager_from_settings(live_settings)


@pytest.fixture(scope="session")
def wsl_keepalive(live_settings: Settings) -> Iterator[None]:
    """WSL stops an idle distribution (and the engine with it); keep one idle session open (plan §3.10)."""
    proc = subprocess.Popen(
        ["wsl.exe", "-d", live_settings.sandbox.wsl_distribution, "--exec", "sleep", "infinity"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def engine(live_settings: Settings, live_secrets: SecretsManager, wsl_keepalive: None) -> Iterator[Engine]:
    deadline = time.monotonic() + 60
    while True:
        try:
            eng = connect(live_settings, live_secrets)
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)
    try:
        yield eng
    finally:
        eng.close()


@pytest.fixture(autouse=True)
def release_wsl_page_cache(request: pytest.FixtureRequest) -> None:
    """Test-environment housekeeping (KI-039): container runs leave page cache in the WSL VM, and without
    ``autoMemoryReclaim`` in ``.wslconfig`` (D-45, owner-managed) Windows does not get it back, so the
    Resource Guard would correctly make later tests wait. Before each Docker test, if free memory is low,
    ask WSL to drop its cache. No platform control is changed."""
    if request.node.get_closest_marker("docker") is None:
        return
    import psutil  # noqa: PLC0415

    if psutil.virtual_memory().available // (1024 * 1024) >= 3072:
        return
    settings = load_settings()
    subprocess.run(
        ["wsl.exe", "-d", settings.sandbox.wsl_distribution, "-u", "root", "--", "sh", "-c",
         "sync; echo 3 > /proc/sys/vm/drop_caches"],
        capture_output=True, check=False, timeout=120,
    )  # fmt: skip
    for _ in range(30):  # Windows reclaims the freed VM memory after a short delay
        if psutil.virtual_memory().available // (1024 * 1024) >= 3072:
            return
        time.sleep(1)


@pytest.fixture(scope="session")
def image_lock(live_settings: Settings) -> ImageLock:
    return ImageLock.load(live_settings.sandbox_image_lock_path)


@pytest.fixture(scope="session")
def runner(engine: Engine, image_lock: ImageLock) -> SandboxRunner:
    return SandboxRunner(engine, image_lock)


def limits(**kw: float) -> ContainerLimits:
    base = {
        "memory_mb": 512,
        "cpus": 1.0,
        "pids": 128,
        "wall_clock_s": 60,
        "tmpfs_mb": 64,
        "work_copy_mb": 64,
        "output_kb": 256,
    }
    return ContainerLimits.model_validate({**base, **kw})


def python_probe(code: str, **kw: object) -> RunRequest:
    """A production-path run of ``python -c <code>`` in the python_api image."""
    args: dict[str, object] = {
        "image": "python_api",
        "argv": ("python", "-c", code),
        "workdir": "/work",
        "env": {"CI": "1"},
        "files": {"probe/readme.txt": b"probe"},
        "limits": limits(),
        "execution_id": new_id("sbx"),
        "labels": {"devteam.test": "isolation"},
    }
    args.update(kw)
    return RunRequest(**args)  # type: ignore[arg-type]


def run_raw(
    engine: Engine,
    image_tag: str,
    code: str,
    *,
    host_overrides: dict[str, object] | None = None,
    create_overrides: dict[str, object] | None = None,
    memory_mb: int = 512,
    pids: int = 128,
) -> tuple[int, str]:
    """NEGATIVE CONTROLS ONLY: the production container with exactly one control changed.

    Proves each isolation probe detects the absence of the control it tests. Never used by the platform.
    """
    spec = ContainerSpec(
        image=image_tag,
        argv=("python", "-c", code),
        workdir="/work",
        env={},
        limits=limits(memory_mb=memory_mb, pids=pids),
        execution_id=new_id("sbx"),
        labels={"devteam.test": "negative-control"},
    )
    host = {**host_config_arguments(spec), **(host_overrides or {})}
    host = {key: value for key, value in host.items() if value is not None}
    engine.api.create_volume(name=spec.volume_name, driver="local", labels=spec.all_labels)
    container = None
    try:
        container = engine.api.create_container(
            host_config=engine.api.create_host_config(**host),
            **{**create_arguments(spec), **(create_overrides or {})},
        )["Id"]
        engine.api.start(container)
        code_ = int(engine.api.wait(container, timeout=120)["StatusCode"])
        return code_, engine.api.logs(container).decode("utf-8", "replace")
    finally:
        engine.remove(container, spec.volume_name)


def wsl_root(settings: Settings, command: str, stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["wsl.exe", "-d", settings.sandbox.wsl_distribution, "-u", "root", "--", "sh", "-c", command],
        input=stdin,
        capture_output=True,
        check=True,
        timeout=300,
    )
    return proc.stdout


@pytest.fixture(scope="session")
def rogue_tls(
    live_settings: Settings, tmp_path_factory: pytest.TempPathFactory, wsl_keepalive: None
) -> dict[str, Path | str]:
    """A second, unrelated CA with its own client certificate (key encrypted too), for negative controls."""
    out = tmp_path_factory.mktemp("rogue_tls")
    passphrase = "rogue-test-passphrase-0123456789"
    script = """set -eu
umask 077
W=$(mktemp -d)
cd "$W"
IFS= read -r P
openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout ca-key.pem \
  -out ca.pem -days 2 -subj "/CN=rogue CA" -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign" 2>/dev/null
printf '%s' "$P" | openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 \
  -aes-256-cbc -pass stdin -out key.pem
printf '%s' "$P" | openssl req -new -key key.pem -passin stdin -out c.csr -subj "/CN=rogue"
printf '%s\\n' "extendedKeyUsage=clientAuth" > c.ext
openssl x509 -req -in c.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 2 -extfile c.ext \
  -out cert.pem 2>/dev/null
tar -cf - ca.pem cert.pem key.pem
cd /; rm -rf "$W"
"""
    wsl_root(live_settings, "umask 077; cat > /root/devteam-rogue.sh", stdin=script.encode())
    try:
        blob = wsl_root(live_settings, "sh /root/devteam-rogue.sh", stdin=(passphrase + "\n").encode())
    finally:
        wsl_root(live_settings, "rm -f /root/devteam-rogue.sh")
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member)
            assert data is not None
            (out / member.name).write_bytes(data.read())
    return {"dir": out, "passphrase": passphrase}
