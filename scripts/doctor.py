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
from pathlib import Path
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


@dataclass(frozen=True)
class SandboxProbe:
    """What the doctor learned about the WSL Docker Engine (Part E). Never contains key material."""

    reachable: bool
    detail: str
    version: str = ""
    operating_system: str = ""
    certificate_not_after: str = ""


SandboxProber = Callable[[Settings], SandboxProbe]
WSL_MEMORY_CAP_GB = 4  # D-45


def probe_sandbox(settings: Settings) -> SandboxProbe:
    """Connect to the engine exactly as the platform does (mutual TLS, pinned CA). Read-only."""
    import socket  # noqa: PLC0415
    import ssl  # noqa: PLC0415

    from devteam_harness.errors import HarnessError  # noqa: PLC0415
    from devteam_harness.sandbox.engine import connect  # noqa: PLC0415
    from devteam_harness.sandbox.tls import build_client_context  # noqa: PLC0415
    from devteam_harness.secrets import SecretsUnavailableError, secrets_manager_from_settings  # noqa: PLC0415

    try:
        secrets = secrets_manager_from_settings(settings)
        with connect(settings, secrets, timeout=15) as engine:
            info = engine.info
        context = build_client_context(settings.sandbox_tls_dir, secrets)
        port = int(settings.sandbox.docker_host.rsplit(":", 1)[1])
        with (
            socket.create_connection(("127.0.0.1", port), timeout=10) as raw,
            context.wrap_socket(raw, server_hostname="127.0.0.1") as tls,
        ):
            not_after = str((tls.getpeercert() or {}).get("notAfter", ""))
    except (HarnessError, SecretsUnavailableError, OSError, ssl.SSLError) as exc:
        message = getattr(exc, "message", None) or type(exc).__name__
        return SandboxProbe(False, str(message))
    return SandboxProbe(True, "ok", info.version, info.operating_system, not_after)


def _wslconfig_memory_gb(path: Path) -> float | None:
    if not path.is_file():
        return None
    in_wsl2 = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("["):
            in_wsl2 = line.lower() == "[wsl2]"
        elif in_wsl2 and line.lower().startswith("memory"):
            match = re.fullmatch(r"memory\s*=\s*([0-9.]+)\s*(gb|mb)", line.lower())
            if match:
                value = float(match.group(1))
                return value if match.group(2) == "gb" else value / 1024
    return None


def check_sandbox(settings: Settings, prober: SandboxProber, environ: Mapping[str, str]) -> list[Check]:
    """Part E: the WSL Docker Engine over mutual TLS (D-07 (b), D-50), the WSL memory cap (D-45), images."""
    import datetime as _dt  # noqa: PLC0415

    checks: list[Check] = []
    if not settings.sandbox.enabled:
        return [Check("sandbox", "warn", "disabled in configuration; sandbox work is Blocked")]
    probe = prober(settings)
    if probe.reachable:
        checks.append(
            Check(
                "sandbox.engine",
                "ok",
                f"Docker Engine {probe.version} on {probe.operating_system} (mutual TLS)",
            )
        )
        status: Status = "ok"
        detail = f"valid until {probe.certificate_not_after}"
        try:
            expires = _dt.datetime.strptime(probe.certificate_not_after, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=_dt.UTC
            )
            if expires - _dt.datetime.now(_dt.UTC) < _dt.timedelta(days=30):
                status, detail = (
                    "warn",
                    f"expires {probe.certificate_not_after}; run setup_wsl_docker.py --renew",
                )
        except ValueError:
            status, detail = "warn", "could not read the certificate expiry"
        checks.append(Check("sandbox.certificates", status, detail))
    else:
        checks.append(
            Check(
                "sandbox.engine",
                "warn",
                f"not usable: {probe.detail}. Sandbox work is Blocked until it is fixed "
                "(start ./scripts/dev.ps1 or run scripts/setup_wsl_docker.py; "
                "see docs/runbooks/docker-setup.md)",
            )
        )
    profile = environ.get("USERPROFILE")
    memory = _wslconfig_memory_gb(Path(profile) / ".wslconfig") if profile else None
    if memory is None:
        checks.append(
            Check(
                "sandbox.wsl_memory",
                "warn",
                f"no [wsl2] memory cap in .wslconfig (recommended {WSL_MEMORY_CAP_GB}GB, D-45)",
            )
        )
    elif memory > WSL_MEMORY_CAP_GB:
        checks.append(
            Check("sandbox.wsl_memory", "warn", f"{memory:g} GB (recommended {WSL_MEMORY_CAP_GB} GB, D-45)")
        )
    else:
        checks.append(Check("sandbox.wsl_memory", "ok", f"{memory:g} GB"))
    lock = settings.sandbox_image_lock_path
    if lock.is_file():
        checks.append(
            Check(
                "sandbox.images",
                "ok",
                f"image lock present ({lock.name}); digests are checked before each run",
            )
        )
    else:
        checks.append(
            Check("sandbox.images", "warn", "not built yet: uv run python scripts/build_sandbox_images.py")
        )
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


def check_api_token(settings: Settings) -> Check:
    """HC-15/HC-17: the per-install key is in the OS credential store (D-18). Never prints it."""
    from devteam_harness.iam import API_TOKEN_REF, MIN_TOKEN_LENGTH  # noqa: PLC0415
    from devteam_harness.secrets import SecretsUnavailableError, secrets_manager_from_settings  # noqa: PLC0415

    if settings.api_token_path.exists():
        return Check(
            "api.token",
            "warn",
            f"plaintext key file {settings.api_token_path} still exists; run "
            "uv run python scripts/migrate_api_token.py (the API will not start until it is migrated)",
        )
    try:
        store = secrets_manager_from_settings(settings)
        stored = store.get(API_TOKEN_REF)
    except SecretsUnavailableError as exc:
        return Check("api.token", "fail", f"credential store unavailable: {exc.message}")
    if stored is None:
        return Check(
            "api.token", "warn", "not created yet; it is created in the credential store on first API start"
        )
    if len(stored.reveal()) < MIN_TOKEN_LENGTH:
        return Check("api.token", "fail", "the stored key is too short; delete it from Credential Manager")
    return Check("api.token", "ok", f"stored in {store.backend_description} (value not shown)")


def run_checks(
    settings: Settings,
    *,
    runner: Runner = run_command,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    sandbox_prober: SandboxProber | None = None,
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
    checks += check_sandbox(settings, sandbox_prober or probe_sandbox, env)
    checks += check_memory(settings)
    checks.append(check_disk(settings))
    checks.append(check_api_token(settings))
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
