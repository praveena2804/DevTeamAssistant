"""Environment doctor: verify the developer machine can run DevTeam AI (roadmap §13 Part A).

Usage:  uv run python scripts/doctor.py [--json]

Status levels: ``ok``, ``warn`` (works, but a documented recommendation is not met or the tool is only
needed by a later part), ``fail`` (a Part A prerequisite is missing). Exit code 1 if any check fails.
Read-only: the doctor never changes machine configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx
import psutil

from devteam_core.config import Settings, is_loopback_host, load_settings

Status = Literal["ok", "warn", "fail"]
Runner = Callable[[list[str]], str | None]

OLLAMA_RECOMMENDED = {"OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NUM_PARALLEL": "1"}


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def run_command(args: list[str], timeout: float = 20) -> str | None:
    """Run a command and return stdout+stderr, or None if it is missing, fails or times out."""
    executable = shutil.which(args[0])
    if executable is None:
        return None
    try:
        done = subprocess.run(
            [executable, *args[1:]], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return (done.stdout + done.stderr).strip()


def _version(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(g) for g in match.groups() if g is not None) if match else None


def check_python() -> Check:
    ver = sys.version_info
    ok = (ver.major, ver.minor) == (3, 12)
    return Check("python", "ok" if ok else "fail", f"{platform.python_version()} (required 3.12.x)")


def check_tool(
    name: str, args: list[str], runner: Runner, *, required_major: int | None = None, missing: Status = "fail"
) -> Check:
    output = runner(args)
    if output is None:
        return Check(name, missing, "not found or not runnable")
    version = _version(output)
    first_line = output.splitlines()[0]
    if required_major is not None and (version is None or version[0] != required_major):
        return Check(name, "warn", f"{first_line} (documented version: {required_major}.x)")
    return Check(name, "ok", first_line)


def check_ollama_config(settings: Settings, environ: Mapping[str, str]) -> list[Check]:
    checks: list[Check] = []
    host = urlsplit(settings.ollama.base_url).hostname or ""
    checks.append(
        Check(
            "ollama.base_url",
            "ok" if is_loopback_host(host) else "fail",
            f"{settings.ollama.base_url} (must be loopback)",
        )
    )
    ollama_host = environ.get("OLLAMA_HOST")
    if ollama_host:
        bind = urlsplit(ollama_host if "://" in ollama_host else f"http://{ollama_host}").hostname or ""
        status: Status = "ok" if is_loopback_host(bind) else "fail"
        checks.append(Check("OLLAMA_HOST", status, f"{ollama_host} (server bind address must be loopback)"))
    else:
        checks.append(Check("OLLAMA_HOST", "ok", "unset (Ollama binds 127.0.0.1 by default)"))
    for key, expected in OLLAMA_RECOMMENDED.items():
        value = environ.get(key)
        if value == expected:
            checks.append(Check(key, "ok", value))
        else:
            checks.append(
                Check(
                    key,
                    "warn",
                    f"{value or 'unset'} (Spec §9.3 recommends {expected}; set it, restart Ollama)",
                )
            )
    keep_alive = environ.get("OLLAMA_KEEP_ALIVE")
    checks.append(
        Check(
            "OLLAMA_KEEP_ALIVE",
            "ok" if keep_alive else "warn",
            keep_alive
            or "unset (Spec §9.3 recommends a short keep-alive, e.g. 1m; the gateway also unloads)",
        )
    )
    return checks


def check_ollama_server(settings: Settings, client: httpx.Client) -> Check:
    try:
        response = client.get(f"{settings.ollama.base_url}/api/version", timeout=3)
        response.raise_for_status()
        return Check("ollama.server", "ok", f"reachable, version {response.json().get('version')}")
    except (httpx.HTTPError, ValueError) as exc:
        return Check(
            "ollama.server", "warn", f"not reachable at {settings.ollama.base_url} ({type(exc).__name__})"
        )


def check_docker(runner: Runner) -> list[Check]:
    cli = runner(["docker", "--version"])
    if cli is None:
        return [Check("docker.cli", "warn", "not found (required from Part E; decision D-07 pending)")]
    checks = [Check("docker.cli", "ok", cli.splitlines()[0])]
    server = runner(["docker", "info", "--format", "{{.ServerVersion}}|{{.OperatingSystem}}"])
    if server is None:
        checks.append(Check("docker.engine", "warn", "engine not running (needed from Part E)"))
    else:
        checks.append(Check("docker.engine", "ok", server))
    return checks


def check_memory(settings: Settings) -> list[Check]:
    mem = psutil.virtual_memory()
    available_mb = mem.available // (1024 * 1024)
    total_gb = mem.total / 1024**3
    threshold = settings.resources.min_free_ram_mb
    status: Status = "ok" if available_mb >= threshold else "warn"
    return [
        Check("ram.total", "ok", f"{total_gb:.1f} GB"),
        Check(
            "ram.available",
            status,
            f"{available_mb} MB (profile '{settings.profile}' wants >= {threshold} MB)",
        ),
        Check("cpu", "ok", f"{psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads"),
    ]


def check_disk(settings: Settings) -> Check:
    target = settings.data_dir if settings.data_dir.exists() else settings.root
    free_gb = shutil.disk_usage(target).free / 1024**3
    return Check("disk.free", "ok" if free_gb >= 10 else "warn", f"{free_gb:.0f} GB free at {target}")


def run_checks(
    settings: Settings,
    *,
    runner: Runner = run_command,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> list[Check]:
    env = os.environ if environ is None else environ
    checks = [
        check_python(),
        check_tool("uv", ["uv", "--version"], runner),
        check_tool("node", ["node", "--version"], runner, required_major=22),
        check_tool("npm", ["npm", "--version"], runner),
        check_tool("git", ["git", "--version"], runner),
        check_tool("gitleaks", ["gitleaks", "version"], runner, missing="warn"),
        check_tool("ollama.cli", ["ollama", "--version"], runner, missing="warn"),
    ]
    checks += check_ollama_config(settings, env)
    owns_client = client is None
    http = client or httpx.Client()
    try:
        checks.append(check_ollama_server(settings, http))
    finally:
        if owns_client:
            http.close()
    checks += check_docker(runner)
    checks += check_memory(settings)
    checks.append(check_disk(settings))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    checks = run_checks(load_settings())
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        width = max(len(c.name) for c in checks)
        for c in checks:
            print(f"[{c.status.upper():4}] {c.name:<{width}}  {c.detail}")
        counts = {s: sum(c.status == s for c in checks) for s in ("ok", "warn", "fail")}
        print(f"\n{counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail")
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
