# Sandbox architecture (HC-14, Part E)

Generated code runs only in disposable Docker containers on the **free, open-source Docker Engine inside
WSL2** (`Ubuntu-24.04`, Ubuntu's `docker.io` 29.1.3). Docker Desktop is not used. There is no host
fallback: if any safety precondition fails, the work is **Blocked** and nothing runs anywhere else.

## Components

| Component | Module | Role |
|---|---|---|
| Engine connection | `devteam_harness.sandbox.engine` (only module importing the Docker SDK) | Mutual TLS to `127.0.0.1:2376`; refuses Docker Desktop, non-Linux engines, unpinned API versions; narrow, typed operations |
| TLS context | `devteam_harness.sandbox.tls` | Private CA only; encrypted client key decrypted in memory with the passphrase from Windows Credential Manager (ADR-0008) |
| Container spec | `devteam_harness.sandbox.spec` | One function builds every container; the engine's record is re-checked before anything starts |
| Runner | `devteam_harness.sandbox.runner` | Create → check → copy in (archive API) → start → watchdog → collect → **always** remove |
| Images | `sandbox/images/*`, `devteam_harness.sandbox.images` | Two digest-locked images; built only by `scripts/build_sandbox_images.py` (the only step with network) |
| Licence policy | `sandbox/images/licence_policy.yaml`, `devteam_harness.sandbox.licences` | Tiered policy (plan §12.3), enforced at build and re-checked by a contract test |
| Resource Guard | `devteam_harness.sandbox.guard`, `devteam_harness.budgets.resource_lease` | Cross-process LLM/sandbox lease (SQLite, D-46), Ollama unload, free-memory check |
| Recipes | `devteam_harness.sandbox.recipes` | The ten allowlisted argument lists; no shell, no package manager at run time |
| Reports | `devteam_harness.sandbox.results` | JUnit/coverage parsing; DTD/entity declarations refused; counts derived from test cases |
| Execution service | `devteam_harness.sandbox.service` | Guard → engine → runner → reports → evidence → `SandboxExecution` record + audit |
| Execute tools | `devteam_harness.sandbox.tools` | `request_build`, `request_test_run`, `request_migration_check` (Tool Layer; per-role policy grants) |
| Migration Runner / Test Runner base | `devteam_services.migration_runner`, `devteam_services.test_runner` | Deterministic services on top of the execution service |
| Integration/Build Service base | `devteam_services.integration_builder` | Starter-kit assembly, materialisation, protected-file checksums, `.env` rendering |
| Starter-kit verification | `devteam_services.templates`, worker, `routers/sandbox.py`, `SandboxPage` | Queued by a person, run by the worker, shown in the UI |

## Fixed container controls

| Control | Value | Verified by |
|---|---|---|
| Network | `network_mode=none` (loopback only) | `test_no_network_outbound_tcp_and_dns_fail` + bridge negative control |
| Root filesystem | read-only | `test_root_filesystem_is_read_only_and_no_host_paths` + writable-root negative control |
| User | `10001:10001`; all capabilities dropped; `no-new-privileges` | `test_runs_as_non_root_without_capabilities` + root negative control |
| `/tmp` | tmpfs, `noexec,nosuid,nodev` | `test_tmp_is_not_executable` + `exec` negative control |
| Mounts | one fresh, labelled per-run volume at `/work`; no host bind mounts; never the Docker socket | S-3 tests, `locked_down_problems` |
| Memory / swap | hard limit, no swap on top (`low_memory` 1,536 MB) | `test_memory_limit_kills_the_process` (+ enough-memory control) |
| CPU / PIDs / wall clock | 1.5 CPUs, 256 PIDs, 600 s on `low_memory` | PID and timeout tests (+ controls) |
| Environment | only what the recipe declares | host-canary test |
| Cleanup | container and volume removed in `finally`; startup sweep by label | cleanup, cancel and sweep tests |
| Image | must equal the locked image ID | digest-mismatch and unknown-image tests |

## Measurement

The images' fixed launcher `/opt/devteam/measure` runs the recipe's argument list (no string is evaluated)
and then records the container's own cgroup counters (`memory.peak`, `memory.events oom_kill`) in
`/work/.devteam/sandbox-metrics.json`. This gives a correct peak memory for very short processes and
recognises out-of-memory kills of child processes, which Docker's own `OOMKilled` flag misses when an init
process is used. The metrics are informational and can never turn a failure into a success.

## Offline React builds

Node packages are installed at build time into the image's read-only `/node_modules`, from a lock file
generated on Linux. A project copied to `/work/frontend` resolves them through Node's standard
parent-folder lookup. Vite and Vitest are started with `--configLoader runner`, so they do not write a
temporary bundled config into the read-only image. Recipes never call `npm` or `npx`.

## Availability and blocking

| Situation | Result |
|---|---|
| Engine not reachable, TLS material missing, Docker Desktop detected, image missing or altered | **Blocked** (`SandboxExecution` status `blocked`, nothing ran) |
| An LLM call holds the lease, Ollama will not release its model, free memory below `sandbox.min_free_ram_mb` | **Waiting for resources** (nothing ran, nothing recorded) |
| WSL stopped an idle distribution | Blocked until `scripts/dev.ps1` (or an idle `wsl` session) keeps it open |
