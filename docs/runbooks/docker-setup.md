# Runbook: Docker Engine in WSL2 for the sandbox (Part E)

The sandbox uses the free, open-source Docker Engine inside the `Ubuntu-24.04` WSL2 distribution
(ADR-0007, ruling D-07 (b)). **Docker Desktop is not used**; the platform refuses its engine.

## One-time set-up

| Step | Command (PowerShell, repository root) | Notes |
|---|---|---|
| 1. Optional, recommended: WSL memory cap (D-45) | Create `%USERPROFILE%\.wslconfig` with the block below, then `wsl --shutdown` | The platform never writes this file |
| 2. Engine, certificates, TLS listener | `uv run python scripts/setup_wsl_docker.py` | Installs `docker.io` + `docker-buildx` from Ubuntu's archive (if missing), creates the certificates, stores the key passphrase in Windows Credential Manager, enables `tcp://127.0.0.1:2376` with `tlsverify`. Runs commands as root inside WSL |
| 3. Check | `uv run python scripts/doctor.py` | `sandbox.engine` and `sandbox.certificates` must be `OK` |
| 4. Images (E.2) | `uv run python scripts/build_sandbox_images.py` | The only step that downloads packages (§ "Network use") |

```ini
[wsl2]
memory=4GB
processors=2
swap=2GB
autoMemoryReclaim=gradual
```

If Docker Desktop's "WSL integration" is enabled for `Ubuntu-24.04`, turn it off (Docker Desktop →
Settings → Resources → WSL integration) or simply leave Docker Desktop closed, so the two engines do not
clash. Uninstalling Docker Desktop is optional.

## Day-to-day

- `./scripts/dev.ps1` keeps the distribution open (WSL stops an idle distribution and the engine with it).
- Without it, start one idle session yourself: `wsl -d Ubuntu-24.04 --exec sleep infinity` (leave it open).
- If the engine cannot be reached, sandbox work is **Blocked** with the reason. Nothing runs on the laptop
  instead.

## Certificates

| File | Where | Secret? |
|---|---|---|
| CA certificate | `data/sandbox_tls/ca.pem` and `/etc/docker/tls/ca.pem` | No |
| Client certificate | `data/sandbox_tls/client-cert.pem` | No |
| Client key | `data/sandbox_tls/client-key.enc.pem` | Stored **encrypted** only |
| Client key passphrase | Windows Credential Manager, entry `devteam-ai` / `platform/sandbox.docker_client_key` | Yes; never in files, logs or the console |
| Server key | `/etc/docker/tls/server-key.pem` inside WSL (root only) | Yes; never leaves WSL |
| CA key | Deleted after issuance | — |

- **Expiry:** 2 years. The doctor warns 30 days ahead.
- **Renew or rotate** (also after any suspected exposure): `uv run python scripts/setup_wsl_docker.py --renew`.
  The engine restarts; old certificates and the old passphrase stop working.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Doctor: `sandbox.engine` not usable, "not reachable" | Start `./scripts/dev.ps1` (or the idle WSL session above). Inside WSL: `sudo systemctl status docker` |
| Doctor: "sandbox TLS files missing" | Run `scripts/setup_wsl_docker.py` |
| Doctor: "passphrase is not in the credential store" | Run `scripts/setup_wsl_docker.py --renew` |
| "Docker Desktop is not used (D-07 (b))" | The connection reached Docker Desktop's engine. Close Docker Desktop; check `sandbox.docker_host` is `tcp://127.0.0.1:2376` |
| `sandbox.wsl_memory` warning | Add the `.wslconfig` block above, then `wsl --shutdown` |
| Sandbox work waits with `resource.low_memory` although nothing else is running | WSL keeps file cache and does not give memory back to Windows quickly (KI-039). Set `autoMemoryReclaim=gradual` in `.wslconfig` (block above), or free the cache now: `wsl -d Ubuntu-24.04 -u root -- sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"`. The free-memory threshold (`sandbox.min_free_ram_mb`) is a safety control; do not lower it to get past this |
| A run is Blocked with `sandbox.image_missing` or `sandbox.image_digest_mismatch` | Run `uv run python scripts/build_sandbox_images.py` (section below) |

## Images

| Image | Contents | Used by |
|---|---|---|
| `devteam-sandbox/python_api:1` | `python:3.12-slim` (pinned by digest); hash-pinned wheels from `sandbox/images/python_api/requirements.lock`; the platform's checking tools | `pytest_api`, `pytest_db`, `alembic_*`, `ruff_check`, `bandit_scan`, `static_validation` |
| `devteam-sandbox/node_web:1` | `node:22-slim` (pinned by digest); `npm ci --ignore-scripts` from `sandbox/images/node_web/package-lock.json` | `tsc_check`, `vitest_run`, `npm_build` |

- **Build or rebuild:** `uv run python scripts/build_sandbox_images.py`. This is the only sandbox step that uses the
  network. It waits for the engine after a cold start, checks every package licence against
  `sandbox/images/licence_policy.yaml`, runs `pip-audit` and `npm audit` on the locks, and records the image IDs in
  `sandbox/images/images.lock.json`. A run refuses any image whose ID does not match the lock.
- **Change a dependency:** edit `requirements.in` or `package.json`, regenerate the lock
  (`--relock-node` for Node; the Node lock is made inside a Linux container), rebuild, and commit the lock files,
  `images.lock.json` and `licence_report.json` together.
- **Offline:** once built, nothing in a sandbox run downloads anything; React builds use the image's
  `/node_modules` through Node's normal parent-folder lookup.

## Network use

The engine itself and every sandbox container run **offline**. Downloads happen only during set-up and
image builds, from free public sources with no account: Ubuntu archive (engine packages), PyPI (Docker SDK,
Python image packages), npm registry (Node image packages), Docker Hub anonymous pulls (base images), and
public advisory databases for `pip-audit` / `npm audit`.
