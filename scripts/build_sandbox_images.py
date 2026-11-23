"""Build the two sandbox images and lock them (Part E plan §3.5, §12; D-08). Owner-run, trusted step.

Usage:
    uv run python scripts/build_sandbox_images.py                # build, check licences, audit, lock
    uv run python scripts/build_sandbox_images.py --relock-node  # first regenerate node_web/package-lock.json

This is the **only** step that uses the network (Docker Hub anonymous pulls of the pinned base images, PyPI,
npm registry, public advisory databases). Every sandbox run afterwards is offline. Steps, stopping at the
first failure (nothing is locked unless everything passed):

1. (``--relock-node`` or missing lock) generate ``package-lock.json`` **inside** the pinned ``node:22-slim``
   image, so native packages are resolved for Linux; ``--ignore-scripts``;
2. build ``devteam-sandbox/python_api:1`` and ``devteam-sandbox/node_web:1`` from their folders;
3. licence report from the built images (Debian packages, Python distributions, every ``package.json``
   under ``/node_modules``), checked against ``sandbox/images/licence_policy.yaml`` (plan §12.3);
4. vulnerability audit of the locked dependencies on this machine (``pip-audit`` on
   ``requirements.lock``, ``npm audit`` on ``package-lock.json``; no package code runs). Unreachable
   advisory databases are recorded as ``not_run``, never as clean; any known vulnerability stops the build;
5. write ``sandbox/images/images.lock.json`` (image IDs) and ``sandbox/images/licence_report.json``.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import docker

from devteam_core.config import Settings, load_settings
from devteam_harness.sandbox.engine import Engine, connect
from devteam_harness.sandbox.licences import LicencePolicy, PackageLicence, evaluate
from devteam_harness.secrets import secrets_manager_from_settings

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "sandbox" / "images"
TOOLS = REPO / "sandbox" / "tools"
TAG_VERSION = "1"
NODE_BASE = re.search(r"^FROM (\S+)$", (IMAGES / "node_web" / "Dockerfile").read_text(), re.M).group(1)  # type: ignore[union-attr]


class BuildError(Exception):
    pass


def _tar(files: dict[str, bytes], executable: frozenset[str] = frozenset()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if name in executable else 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _members(chunks: Any) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:")


# -------------------------------------------------------------------------------------- 1. node lock


def relock_node(engine: Engine) -> None:
    api = engine.api
    package = (IMAGES / "node_web" / "package.json").read_bytes()
    container = api.create_container(
        NODE_BASE,
        command=["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        working_dir="/opt/build",
        host_config=api.create_host_config(network_mode="bridge", cap_drop=["ALL"]),
    )["Id"]
    try:
        api.put_archive(container, "/", _tar({"opt/build/package.json": package}))
        api.start(container)
        code = api.wait(container, timeout=600)["StatusCode"]
        if code != 0:
            raise BuildError(
                "npm could not resolve the node_web lock: " + api.logs(container).decode()[-800:]
            )
        stream, _ = api.get_archive(container, "/opt/build/package-lock.json")
        with _members(stream) as tar:
            member = tar.extractfile("package-lock.json")
            if member is None:
                raise BuildError("npm produced no package-lock.json")
            (IMAGES / "node_web" / "package-lock.json").write_bytes(member.read())
    finally:
        api.remove_container(container, force=True, v=True)
    print("node_web/package-lock.json regenerated inside node:22-slim (Linux)")


# -------------------------------------------------------------------------------------- 2. build


def build_context(name: str) -> bytes:
    folder = IMAGES / name
    files = {
        "Dockerfile": (folder / "Dockerfile").read_bytes(),
        "measure": (IMAGES / "measure.sh").read_bytes(),
    }
    if name == "python_api":
        files["requirements.lock"] = (folder / "requirements.lock").read_bytes()
        for tool in sorted(TOOLS.glob("*.py")):
            files[f"tools/{tool.name}"] = tool.read_bytes()
    else:
        files["package.json"] = (folder / "package.json").read_bytes()
        files["package-lock.json"] = (folder / "package-lock.json").read_bytes()
    return _tar(files, executable=frozenset({"measure"}))


def build_image(engine: Engine, name: str) -> str:
    tag = f"devteam-sandbox/{name}:{TAG_VERSION}"
    started = time.monotonic()
    log: list[str] = []
    for chunk in engine.api.build(
        fileobj=io.BytesIO(build_context(name)),
        custom_context=True,
        tag=tag,
        rm=True,
        forcerm=True,
        pull=False,
        decode=True,
        labels={"devteam.image": name},
    ):
        if "error" in chunk:
            raise BuildError(f"{name}: {chunk['error'].strip()}\n" + "".join(log[-20:]))
        if "stream" in chunk:
            log.append(chunk["stream"])
    image_id = str(engine.api.inspect_image(tag)["Id"])
    print(f"built {tag} {image_id[:19]} in {time.monotonic() - started:.0f}s")
    return image_id


# -------------------------------------------------------------------------------------- 3. licences


def _debian_packages(engine: Engine, container: str) -> list[PackageLicence]:
    stream, _ = engine.api.get_archive(container, "/var/lib/dpkg/status")
    with _members(stream) as tar:
        handle = tar.extractfile("status")
        status = handle.read().decode("utf-8", "replace") if handle else ""
    copyrights: dict[str, str] = {}
    stream, _ = engine.api.get_archive(container, "/usr/share/doc")
    with _members(stream) as tar:
        for member in tar:
            parts = member.name.split("/")
            if member.isreg() and len(parts) == 3 and parts[2] == "copyright":
                handle = tar.extractfile(member)
                text = handle.read().decode("utf-8", "replace") if handle else ""
                names = sorted({m.strip() for m in re.findall(r"^License:\s*(\S.*)$", text, re.M)})
                copyrights[parts[1]] = ", ".join(names)[:300] or "see copyright file"
    rows = []
    for block in status.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z-]+): (.*)$", block, re.M))
        if fields.get("Package") and "installed" in fields.get("Status", ""):
            name = fields["Package"]
            licence = copyrights.get(name, "")
            rows.append(
                PackageLicence(
                    "debian", name, fields.get("Version", ""), licence or "(copyright file not found)", 1
                )
            )
    return rows


def _python_packages(
    engine: Engine, container: str, locked: set[str], policy: LicencePolicy
) -> list[PackageLicence]:
    stream, _ = engine.api.get_archive(container, "/usr/local/lib/python3.12/site-packages")
    rows = []
    with _members(stream) as tar:
        for member in tar:
            if not (
                member.isreg() and member.name.endswith(".dist-info/METADATA") and member.name.count("/") == 2
            ):
                continue
            handle = tar.extractfile(member)
            meta = email.parser.HeaderParser().parsestr(
                handle.read().decode("utf-8", "replace") if handle else ""
            )
            name = meta.get("Name", "")
            classifiers = [
                c.split("::")[-1].strip()
                for c in meta.get_all("Classifier") or []
                if c.startswith("License ::")
            ]
            free_text = (meta.get("License") or "").strip().splitlines()
            licence = (
                meta.get("License-Expression")
                or " / ".join(classifiers)
                or (free_text[0][:80] if free_text else "")
            )
            key = name.lower().replace("_", "-")
            tier = 4 if policy.is_tier4("python", name) else 3 if key in locked else 2
            rows.append(PackageLicence("python", name, meta.get("Version", ""), str(licence), tier))
    return rows


def _node_packages(engine: Engine, container: str, policy: LicencePolicy) -> list[PackageLicence]:
    stream, _ = engine.api.get_archive(container, "/node_modules")
    rows = []
    pattern = re.compile(r"^(?:.*/)?node_modules/((?:@[^/]+/)?[^/@][^/]*)/package\.json$")
    with _members(stream) as tar:
        for member in tar:
            match = pattern.match(member.name)
            if not member.isreg() or not match:
                continue
            handle = tar.extractfile(member)
            try:
                data = json.loads(handle.read() if handle else b"{}")
            except json.JSONDecodeError:
                continue
            if data.get("name") != match.group(1) or "version" not in data:
                continue  # nested test fixtures and partial manifests are not installed packages
            licence = data.get("license")
            if isinstance(licence, dict):
                licence = licence.get("type", "")
            if not licence and isinstance(data.get("licenses"), list):
                licence = " OR ".join(
                    str(item.get("type", "")) for item in data["licenses"] if isinstance(item, dict)
                )
            tier = 4 if policy.is_tier4("node", data["name"]) else 3
            rows.append(PackageLicence("node", data["name"], str(data["version"]), str(licence or ""), tier))
    unique = {(r.name, r.version): r for r in rows}
    return sorted(unique.values(), key=lambda r: (r.name, r.version))


def licence_report(
    engine: Engine, name: str, policy: LicencePolicy
) -> tuple[list[PackageLicence], list[str]]:
    api = engine.api
    container = api.create_container(
        f"devteam-sandbox/{name}:{TAG_VERSION}", command=["true"], network_disabled=True
    )["Id"]
    try:
        rows = _debian_packages(engine, container)
        if name == "python_api":
            locked = {
                line.split("==")[0].strip().lower()
                for line in (IMAGES / "python_api" / "requirements.lock").read_text().splitlines()
                if re.match(r"^[a-z0-9]", line)
            }
            rows += _python_packages(engine, container, locked, policy)
        else:
            rows += _node_packages(engine, container, policy)
    finally:
        api.remove_container(container, force=True, v=True)
    return rows, evaluate(rows, policy)


# -------------------------------------------------------------------------------------- 4. audits


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    exe = shutil.which(args[0])
    if exe is None:
        raise BuildError(f"{args[0]} not found")
    return subprocess.run(
        [exe, *args[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        check=False,
        timeout=900,
    )


def audit_python() -> dict[str, Any]:
    result = _run(
        [
            "uvx",
            "pip-audit",
            "--progress-spinner",
            "off",
            "--format",
            "json",
            "--disable-pip",
            "--require-hashes",
            "-r",
            str(IMAGES / "python_api" / "requirements.lock"),
        ]
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"tool": "pip-audit", "status": "not_run", "reason": result.stderr.strip()[-300:]}
    vulns = [
        {"package": d["name"], "version": d["version"], "ids": [v["id"] for v in d.get("vulns", [])]}
        for d in data.get("dependencies", [])
        if d.get("vulns")
    ]
    return {
        "tool": "pip-audit",
        "status": "clean" if not vulns else "vulnerable",
        "vulnerabilities": vulns,
        "checked": len(data.get("dependencies", [])),
    }


def audit_node() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        for file in ("package.json", "package-lock.json"):
            shutil.copy2(IMAGES / "node_web" / file, Path(tmp) / file)
        result = _run(
            ["npm.cmd" if sys.platform == "win32" else "npm", "audit", "--package-lock-only", "--json"],
            cwd=Path(tmp),
        )
    try:
        data = json.loads(result.stdout)
        counts = data["metadata"]["vulnerabilities"]
    except (json.JSONDecodeError, KeyError):
        return {
            "tool": "npm audit",
            "status": "not_run",
            "reason": (result.stderr or result.stdout).strip()[-300:],
        }
    found = {k: v for k, v in counts.items() if k != "total" and v}
    return {
        "tool": "npm audit",
        "status": "clean" if not found else "vulnerable",
        "vulnerabilities": found,
        "advisories": sorted(data.get("vulnerabilities", {})),
    }


# -------------------------------------------------------------------------------------- main


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--relock-node", action="store_true")
    args = parser.parse_args(argv)
    settings: Settings = load_settings()
    policy = LicencePolicy.load(IMAGES / "licence_policy.yaml")
    keepalive = subprocess.Popen(
        ["wsl.exe", "-d", settings.sandbox.wsl_distribution, "--exec", "sleep", "infinity"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        time.sleep(3)
        with connect(settings, secrets_manager_from_settings(settings), timeout=900) as engine:
            if args.relock_node or not (IMAGES / "node_web" / "package-lock.json").is_file():
                relock_node(engine)
            images: dict[str, Any] = {}
            report: dict[str, Any] = {}
            for name in ("python_api", "node_web"):
                image_id = build_image(engine, name)
                rows, problems = licence_report(engine, name, policy)
                if problems:
                    raise BuildError(f"{name}: licence policy violations:\n  " + "\n  ".join(problems))
                report[name] = [
                    {
                        "ecosystem": r.ecosystem,
                        "name": r.name,
                        "version": r.version,
                        "licence": r.licence,
                        "tier": r.tier,
                    }
                    for r in rows
                ]
                images[name] = {
                    "tag": f"devteam-sandbox/{name}:{TAG_VERSION}",
                    "image_id": image_id,
                    "dockerfile_sha256": _sha(IMAGES / name / "Dockerfile"),
                    "lock_sha256": _sha(
                        IMAGES / name / ("requirements.lock" if name == "python_api" else "package-lock.json")
                    ),
                    "packages_by_tier": {str(t): sum(r.tier == t for r in rows) for t in (1, 2, 3, 4)},
                    "reviewed_tier3": sorted({r.name for r in rows if r.name in policy.tier3_reviewed}),
                }
                print(f"{name}: licences OK ({len(rows)} packages)")
            audits = {"python_api": audit_python(), "node_web": audit_node()}
            for name, audit in audits.items():
                images[name]["audit"] = audit
                print(f"{name}: audit {audit['status']}")
            if any(a["status"] == "vulnerable" for a in audits.values()):
                raise BuildError(
                    "known vulnerabilities in the locked dependencies:\n" + json.dumps(audits, indent=2)
                )
            lock = {
                "schema": 1,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "engine": engine.info.version,
                "images": images,
            }
            (IMAGES / "images.lock.json").write_text(
                json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            (IMAGES / "licence_report.json").write_text(
                json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n"
            )
    except (BuildError, docker.errors.DockerException) as exc:
        print(f"image build failed: {exc}", file=sys.stderr)
        return 1
    finally:
        keepalive.terminate()
    print("images built, checked and locked: sandbox/images/images.lock.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
