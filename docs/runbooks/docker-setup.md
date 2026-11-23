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

## Network use

The engine itself and every sandbox container run **offline**. Downloads happen only during set-up and
image builds, from free public sources with no account: Ubuntu archive (engine packages), PyPI (Docker SDK,
Python image packages), npm registry (Node image packages), Docker Hub anonymous pulls (base images), and
public advisory databases for `pip-audit` / `npm audit`.
