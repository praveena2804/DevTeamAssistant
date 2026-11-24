"""Container specification for one sandbox run (HC-14; Part E plan §3.2; TS-04). Pure: no I/O.

Every container is described by exactly one function, :func:`create_arguments`, and the engine's own view
of the created container is checked again by :func:`locked_down_problems` before anything starts. The
fixed controls below are not configurable:

- ``network_mode="none"`` (only the loopback interface exists);
- read-only root filesystem; writable areas are the per-run ``/work`` volume and a ``noexec`` ``/tmp``;
- user ``10001:10001``; all capabilities dropped; ``no-new-privileges``; not privileged; no devices;
  private IPC; host PID/UTS/IPC namespaces never shared; ``docker-init`` reaps zombies;
- **no host mounts**: the only mount is a fresh, labelled per-run Docker volume (D-48);
- the environment is exactly what the recipe declares (the image's own variables remain); nothing from
  the host process is passed through.

Memory, CPU, PIDs, wall clock, ``/tmp`` size and output caps come from the configured profile ceiling; a
recipe may ask for less, never more (:func:`effective_limits`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from devteam_core.config.settings import SandboxLimits
from devteam_core.contracts.sandbox import ContainerLimits

SANDBOX_USER = "10001:10001"
WORK_DIR = "/work"
OUT_DIR = "/work/.devteam/out"
METRICS_FILE = "/work/.devteam/sandbox-metrics.json"
LAUNCHER = "/opt/devteam/measure"  # fixed launcher baked into both images (runs argv as a list)
LABEL = "devteam.sandbox"
_MB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    image: str  # locked image tag, e.g. devteam-sandbox/python_api:1
    argv: tuple[str, ...]
    workdir: str
    env: Mapping[str, str]
    limits: ContainerLimits
    execution_id: str
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def volume_name(self) -> str:
        return f"devteam-sbx-{self.execution_id.lower()}"

    @property
    def all_labels(self) -> dict[str, str]:
        return {**dict(self.labels), LABEL: "1", f"{LABEL}.execution_id": self.execution_id}


def ceiling_of(settings: SandboxLimits) -> ContainerLimits:
    return ContainerLimits.model_validate(settings.model_dump())


def effective_limits(ceiling: ContainerLimits, requested: ContainerLimits | None = None) -> ContainerLimits:
    """The profile ceiling, lowered (never raised) by what a recipe asks for."""
    if requested is None:
        return ceiling
    data = ceiling.model_dump(exclude={"schema_version"})
    for key, value in requested.model_dump(exclude={"schema_version"}).items():
        data[key] = min(data[key], value)
    return ContainerLimits.model_validate(data)


def host_config_arguments(spec: ContainerSpec) -> dict[str, Any]:
    limits = spec.limits
    memory = f"{limits.memory_mb}m"
    return {
        "network_mode": "none",
        "read_only": True,
        "privileged": False,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "ipc_mode": "private",
        "mem_limit": memory,
        "memswap_limit": memory,  # no swap on top of the memory limit
        "nano_cpus": int(limits.cpus * 1_000_000_000),
        "pids_limit": limits.pids,
        "tmpfs": {"/tmp": f"rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m"},
        "mounts": [{"Target": WORK_DIR, "Source": spec.volume_name, "Type": "volume", "ReadOnly": False}],
        "init": True,
        "oom_kill_disable": False,
        "auto_remove": False,  # we remove explicitly after collecting results
        "log_config": {"Type": "json-file", "Config": {"max-size": f"{max(limits.output_kb * 4, 64)}k"}},
    }


def create_arguments(spec: ContainerSpec) -> dict[str, Any]:
    """Keyword arguments for the engine's ``create_container`` (without ``host_config``)."""
    if not spec.argv:
        raise ValueError("a sandbox run needs a command")
    if spec.workdir != WORK_DIR and not spec.workdir.startswith(WORK_DIR + "/"):
        raise ValueError("the working directory must be inside /work")
    return {
        "image": spec.image,
        "command": list(spec.argv),
        "entrypoint": [LAUNCHER],
        "user": SANDBOX_USER,
        "working_dir": spec.workdir,
        "environment": dict(spec.env),
        "labels": spec.all_labels,
        "network_disabled": True,
        "hostname": "sandbox",
        "stdin_open": False,
        "tty": False,
    }


def locked_down_problems(inspected: Mapping[str, Any], spec: ContainerSpec) -> list[str]:
    """Check the engine's record of the created container against every fixed control."""
    host = inspected.get("HostConfig") or {}
    config = inspected.get("Config") or {}
    limits = spec.limits
    problems: list[str] = []

    def expect(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    expect(host.get("NetworkMode") == "none", "network is not 'none'")
    expect(config.get("NetworkDisabled") is True, "networking not disabled")
    expect(host.get("ReadonlyRootfs") is True, "root filesystem is writable")
    expect(host.get("Privileged") is False, "container is privileged")
    expect(
        [c.upper() for c in host.get("CapDrop") or []] in (["ALL"], ["CAP_ALL"]),
        "capabilities not all dropped",
    )
    expect(not host.get("CapAdd"), "capabilities were added")
    expect(any("no-new-privileges" in s for s in host.get("SecurityOpt") or []), "no-new-privileges missing")
    expect(host.get("Memory") == limits.memory_mb * _MB, "memory limit differs")
    expect(host.get("MemorySwap") == limits.memory_mb * _MB, "swap is allowed on top of the memory limit")
    expect(host.get("NanoCpus") == int(limits.cpus * 1_000_000_000), "CPU limit differs")
    expect(host.get("PidsLimit") == limits.pids, "PID limit differs")
    expect(not host.get("Binds"), "host bind mounts present")
    expect(not host.get("Devices"), "devices attached")
    expect(host.get("PidMode", "") in {"", "private"}, "host PID namespace shared")
    expect(host.get("UTSMode", "") in {"", "private"}, "host UTS namespace shared")
    expect(host.get("IpcMode") == "private", "IPC namespace not private")
    expect(set((host.get("Tmpfs") or {}).keys()) == {"/tmp"}, "unexpected tmpfs mounts")
    expect("noexec" in (host.get("Tmpfs") or {}).get("/tmp", ""), "/tmp is executable")
    mounts = host.get("Mounts") or []
    expect(
        len(mounts) == 1
        and mounts[0].get("Type") == "volume"
        and mounts[0].get("Target") == WORK_DIR
        and mounts[0].get("Source") == spec.volume_name,
        "mounts other than the per-run /work volume",
    )
    expect(config.get("User") == SANDBOX_USER, "container user is not 10001:10001")
    expect(list(config.get("Entrypoint") or []) == [LAUNCHER], "unexpected entrypoint")
    exposed = [
        *(host.get("Binds") or []),
        *(str(m) for m in inspected.get("Mounts") or []),
        *(str(m) for m in mounts),
        *(config.get("Volumes") or {}),
        *(str(d) for d in host.get("Devices") or []),
    ]
    expect(not any("docker.sock" in str(item) for item in exposed), "the Docker socket is mounted")
    return problems


__all__ = [
    "LABEL",
    "LAUNCHER",
    "METRICS_FILE",
    "OUT_DIR",
    "SANDBOX_USER",
    "WORK_DIR",
    "ContainerSpec",
    "ceiling_of",
    "create_arguments",
    "effective_limits",
    "host_config_arguments",
    "locked_down_problems",
]
