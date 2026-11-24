# ADR-0008: Sandbox engine connection and file transfer

- **Status:** Accepted (owner rulings D-48, D-50, D-51 in AP-021, 2026-11-23; D-50 conditional on spike S-1, which passed)
- **Date:** 2026-11-23
- **Related:** ADR-0007 (Docker Engine in WSL2), `docs/parts/PART-E-PLAN.md` §3.10–§3.12, HC-14

## Context

The platform runs on Windows; the Docker Engine runs inside the `Ubuntu-24.04` WSL2 distribution
(ADR-0007). Windows programs cannot open the engine's Unix socket. The connection must not be reachable by
other local programs or web pages, and generated code must never see host folders.

## Decision

1. **Engine source (D-51):** Ubuntu's own archive packages `docker.io` (29.1.3) and `docker-buildx`
   (Apache-2.0). Installed and configured by `scripts/setup_wsl_docker.py`.
2. **Connection (D-50):** the engine listens on its Unix socket and on `tcp://127.0.0.1:2376` with
   `tlsverify`. WSL's localhost forwarding makes that port reachable from Windows `127.0.0.1` only.
   - A private CA (EC P-256, 2 years) signs one server certificate (`IP:127.0.0.1`, `DNS:localhost`) and one
     client certificate. The CA key is deleted after issuance; the server key stays in `/etc/docker/tls`
     (root only).
   - The client key is generated **directly in encrypted form** (PKCS#8, AES-256). Its random passphrase
     goes to OpenSSL on standard input and is stored only in Windows Credential Manager
     (`secret://platform/sandbox.docker_client_key`).
   - The platform builds a standard-library `ssl.SSLContext` (TLS ≥ 1.2, certificate and host-name
     verification on, the private CA as the only trust anchor), decrypts the key in memory with
     `load_cert_chain(password=…)`, and hands that context to the Docker SDK through a small `requests`
     adapter (`PinnedTLSAdapter`). The SDK's own `TLSConfig` is not used because it cannot load an
     encrypted key. The adapter refuses `verify=False`, client-certificate overrides, proxies and any host
     other than `127.0.0.1`, and never lets requests add its CA bundle. Proxy settings from the environment
     and `~/.docker/config.json` are ignored and never injected into containers.
   - The API version is pinned (`1.44`), so the SDK makes no request before the TLS adapter is in place.
3. **Only one module imports the SDK:** `devteam_harness.sandbox.engine` (import-linter contract).
4. **Engine identity:** an engine that reports Docker Desktop, is not Linux, or does not support the pinned
   API version is refused (Blocked).
5. **File transfer (D-48):** Docker archive API copy-in/copy-out into a per-execution named volume
   (`devteam-sbx-<id>`, labelled `devteam.sandbox=1`, removed with the container); no host bind mounts
   (verified by spike S-3 in E.2).
6. **Availability:** WSL stops an idle distribution, and the engine with it. `scripts/dev.ps1` keeps one
   idle session (`wsl -d Ubuntu-24.04 --exec sleep infinity`) open while the platform runs. An unreachable
   engine means the work is Blocked, never run elsewhere.

## Spike S-1 result (E.1, 2026-11-23): passed

| # | Requirement | Evidence (`tests/sandbox/test_s1_mutual_tls.py`, real engine) |
|---|---|---|
| 1 | Engine reachable through the Windows loopback endpoint; it is the WSL engine | `ping` true; `Os` linux; `OperatingSystem` Ubuntu 24.04.4 LTS; Ubuntu `docker.io` build |
| 2 | Mutual TLS works, with negative controls | Our certificate: `/_ping` 200. Without a client certificate: refused. Client certificate from a foreign CA: refused. Server not signed by the trusted CA: client raises `SSLCertVerificationError` |
| 3 | Encrypted key loads through the intended SDK integration | Key file has only `BEGIN ENCRYPTED PRIVATE KEY`; SDK requests go through `PinnedTLSAdapter`; wrong or missing passphrase fails closed |
| 4 | Passphrase only from Credential Manager | Not in environment, config, `.env`, TLS folder or logs; registered with the redactor |
| 5 | Decrypted key never written to disk | No file with a plaintext-key marker created in `data/`, the repository or `%TEMP%` while connecting |
| 6 | Verification stays enabled | `CERT_REQUIRED`, `check_hostname` true, TLS ≥ 1.2 after real requests; exactly one CA in the store; a weakened context is refused; static AST scan finds no disabled verification in platform code |

14 tests, all passed against the real engine (TR-0110).

## Spike S-2 result (E.2, 2026-11-23): passed

Offline React build in the `node_web` image (`tests/sandbox/test_s2_offline_react.py`, real engine, 6 tests
passed, TR-0117): the type check, Vite build and Vitest run complete with `network=none`. Dependencies come
from the image's `/node_modules` through Node's normal parent-folder lookup, nothing is downloaded, and
`docker diff` shows the image unchanged. Two findings were solved without weakening any control: Vite and
Vitest wrote a temporary config into `/node_modules` (read-only, as intended), so the recipes use
`--configLoader runner`; and Vitest needs `NODE_ENV=test` (React's production build has no `act`).

## Spike S-3 result (E.2, 2026-11-23): passed

Archive transfer and availability (`tests/sandbox/test_s3_transfer.py`, TR-0118, TR-0119): files are copied in
and out through the archive API, owned by the sandbox user, with no host bind mount; output names that escape
the output folder are never collected. Over 30 minutes with the keep-alive session, the engine answered every
one-minute check. After `wsl --shutdown` the connection was refused and reported as Blocked
(`sandbox.docker_unavailable`), never run elsewhere.

## Consequences

- If the client certificate expires (2 years), `scripts/doctor.py` warns 30 days ahead and
  `scripts/setup_wsl_docker.py --renew` re-issues everything.
- **Incident during the spike (KI-036):** a first draft of one test printed the real passphrase in an
  assertion message on the developer console. The test was rewritten so no assertion can show a secret,
  and all certificates and the passphrase were re-issued (`--renew`) the same session; the printed value is
  no longer valid anywhere.
