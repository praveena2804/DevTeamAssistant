"""One-time set-up of Docker Engine inside WSL2 with a mutual-TLS loopback listener (Part E; D-07 (b),
D-50, D-51). Owner-run (or run by the assistant on the owner's instruction, AP-021).

Usage:  uv run python scripts/setup_wsl_docker.py [--renew]

What it does, in order (it stops at the first failure):

1. Checks that the WSL distribution exists and runs systemd.
2. Installs ``docker.io`` and ``docker-buildx`` from Ubuntu's own archive if missing (D-51; free, Apache-2.0).
3. Creates certificates with OpenSSL **inside** Ubuntu: a private CA, a server certificate for 127.0.0.1 /
   localhost (key stays in ``/etc/docker/tls``, root only), and a client certificate whose private key is
   written **only encrypted** (PKCS#8, AES-256). The passphrase is random, is passed to OpenSSL on standard
   input (never on a command line or in a file) and is stored in Windows Credential Manager through the
   platform secret store (``secret://platform/sandbox.docker_client_key``). The CA key is deleted.
4. Makes the engine listen on ``tcp://127.0.0.1:2376`` with ``tlsverify`` (systemd override +
   ``/etc/docker/daemon.json``), next to its normal Unix socket.
5. Copies the CA certificate, client certificate and encrypted client key to ``data/sandbox_tls/``.

It never disables TLS verification and never writes an unencrypted client key. Existing certificates are
kept unless ``--renew`` is given. Platform code never runs this script or any other program (AC-E-04).
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time

from devteam_core.config import Settings, load_settings
from devteam_harness.sandbox.tls import CLIENT_FILES, PASSPHRASE_REF
from devteam_harness.secrets import secrets_manager_from_settings

WORK = "/root/devteam-tls"
TLS_DIR = "/etc/docker/tls"

_CERT_SCRIPT = r"""
set -eu
umask 077
W=__WORK__
rm -rf "$W"; mkdir -p "$W"; cd "$W"
IFS= read -r PASS
[ -n "$PASS" ]
# Private CA (valid 2 years). Its key is deleted at the end of this script.
openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout ca-key.pem \
  -out ca.pem -days 730 -sha256 -subj "/CN=DevTeam AI sandbox CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" 2>/dev/null
# Server certificate for the loopback listener. Key stays root-only inside WSL.
openssl req -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout server-key.pem \
  -out server.csr -subj "/CN=localhost" 2>/dev/null
printf '%s\n' "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" \
  "extendedKeyUsage=serverAuth" "subjectAltName=IP:127.0.0.1,DNS:localhost" \
  "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid,issuer" > server.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 730 -sha256 \
  -extfile server.ext -out server-cert.pem 2>/dev/null
# Client certificate. The key is generated directly in encrypted form; the passphrase arrives on stdin.
printf '%s' "$PASS" | openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 \
  -aes-256-cbc -pass stdin -out client-key.enc.pem
printf '%s' "$PASS" | openssl req -new -key client-key.enc.pem -passin stdin -out client.csr \
  -subj "/CN=devteam-ai-client"
unset PASS
printf '%s\n' "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" \
  "extendedKeyUsage=clientAuth" "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid,issuer" \
  > client.ext
openssl x509 -req -in client.csr -CA ca.pem -CAkey ca-key.pem -CAserial ca.srl -days 730 -sha256 \
  -extfile client.ext -out client-cert.pem 2>/dev/null
grep -q "BEGIN ENCRYPTED PRIVATE KEY" client-key.enc.pem
if grep -q "BEGIN PRIVATE KEY" client-key.enc.pem; then exit 3; fi
install -d -m 700 __TLS__
install -m 644 ca.pem __TLS__/ca.pem
install -m 644 server-cert.pem __TLS__/server-cert.pem
install -m 600 server-key.pem __TLS__/server-key.pem
shred -u ca-key.pem server-key.pem 2>/dev/null || rm -f ca-key.pem server-key.pem
rm -f server.csr client.csr server.ext client.ext ca.srl
""".replace("__WORK__", WORK).replace("__TLS__", TLS_DIR)

_DAEMON = {
    "tlsverify": True,
    "tlscacert": f"{TLS_DIR}/ca.pem",
    "tlscert": f"{TLS_DIR}/server-cert.pem",
    "tlskey": f"{TLS_DIR}/server-key.pem",
}
_OVERRIDE = (
    "# DevTeam AI (Part E, D-50): add the mutual-TLS loopback listener next to the Unix socket.\n"
    "[Service]\nExecStart=\n"
    "ExecStart=/usr/bin/dockerd -H fd:// -H tcp://127.0.0.1:2376 "
    "--containerd=/run/containerd/containerd.sock\n"
)


class SetupError(Exception):
    pass


def _wsl(distro: str, command: str, *, stdin: bytes | None = None, check: bool = True) -> bytes:
    """Run one ``sh -c`` command as root inside the distribution (owner-run script; S603 allowed here)."""
    proc = subprocess.run(
        ["wsl.exe", "-d", distro, "-u", "root", "--", "sh", "-c", command],
        input=stdin,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[-400:]
        raise SetupError(f"step failed in {distro} (exit {proc.returncode}): {detail}")
    return proc.stdout


def _write_root_file(distro: str, path: str, content: bytes, mode: str) -> None:
    _wsl(distro, f"umask 077; cat > '{path}' && chmod {mode} '{path}'", stdin=content)


def check_distribution(distro: str) -> None:
    listing = subprocess.run(
        ["wsl.exe", "-l", "-q"],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout.decode("utf-16-le", "replace")
    names = {line.strip().strip("\x00") for line in listing.splitlines() if line.strip()}
    if distro not in names:
        raise SetupError(f"WSL distribution {distro!r} not found (installed: {sorted(names)})")
    init = _wsl(distro, "ps -p 1 -o comm=").decode().strip()
    if init != "systemd":
        raise SetupError("systemd is not enabled in the distribution (/etc/wsl.conf: [boot] systemd=true)")


def install_engine(distro: str) -> None:
    present = _wsl(distro, "command -v dockerd >/dev/null && echo yes || echo no").decode().strip()
    if present == "yes":
        return
    _wsl(distro, "apt-get update -q")
    _wsl(
        distro,
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "
        "docker.io docker-buildx",
    )


def create_certificates(distro: str, settings: Settings, *, renew: bool) -> None:
    tls_dir = settings.sandbox_tls_dir
    existing = all((tls_dir / name).is_file() for name in CLIENT_FILES.values())
    store = secrets_manager_from_settings(settings)  # fails closed without Credential Manager
    if existing and store.exists(PASSPHRASE_REF) and not renew:
        print("certificates already present (use --renew to replace them)")
        return
    passphrase = secrets.token_urlsafe(32)
    _write_root_file(distro, f"{WORK}.sh", _CERT_SCRIPT.encode(), "700")
    try:
        _wsl(distro, f"sh {WORK}.sh", stdin=(passphrase + "\n").encode())
        files = {key: _wsl(distro, f"cat {WORK}/{name}") for key, name in _SOURCE_FILES.items()}
    finally:
        _wsl(distro, f"rm -rf {WORK} {WORK}.sh", check=False)
    if b"BEGIN ENCRYPTED PRIVATE KEY" not in files["key"] or b"BEGIN PRIVATE KEY" in files["key"]:
        raise SetupError("client key is not encrypted; nothing was stored")
    tls_dir.mkdir(parents=True, exist_ok=True)
    for key, name in CLIENT_FILES.items():
        (tls_dir / name).write_bytes(files[key])
    store.put(PASSPHRASE_REF, passphrase)
    del passphrase


_SOURCE_FILES = {"ca": "ca.pem", "cert": "client-cert.pem", "key": "client-key.enc.pem"}


def configure_daemon(distro: str) -> None:
    current = _wsl(distro, "cat /etc/docker/daemon.json 2>/dev/null || true").strip()
    if current:
        data = json.loads(current)
        unexpected = set(data) - set(_DAEMON)
        if unexpected:
            raise SetupError(
                f"/etc/docker/daemon.json has other settings {sorted(unexpected)}; merge by hand"
            )
    _wsl(distro, "install -d -m 755 /etc/docker /etc/systemd/system/docker.service.d")
    _write_root_file(distro, "/etc/docker/daemon.json", json.dumps(_DAEMON, indent=2).encode(), "644")
    _write_root_file(
        distro, "/etc/systemd/system/docker.service.d/devteam-tls.conf", _OVERRIDE.encode(), "644"
    )
    _wsl(distro, "systemctl daemon-reload && systemctl enable docker.service && systemctl restart docker")
    for _ in range(30):
        listening = _wsl(distro, "ss -ltn 'sport = :2376' | tail -n +2", check=False).decode()
        if "127.0.0.1:2376" in listening:
            return
        time.sleep(1)
    raise SetupError("the engine is not listening on 127.0.0.1:2376 after restart")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--renew", action="store_true", help="replace existing certificates")
    args = parser.parse_args(argv)
    settings = load_settings()
    distro = settings.sandbox.wsl_distribution
    try:
        check_distribution(distro)
        install_engine(distro)
        create_certificates(distro, settings, renew=args.renew)
        configure_daemon(distro)
    except (SetupError, subprocess.SubprocessError, OSError) as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Docker Engine in {distro} listens on 127.0.0.1:2376 with mutual TLS.")
    print("Next: uv run python scripts/doctor.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
