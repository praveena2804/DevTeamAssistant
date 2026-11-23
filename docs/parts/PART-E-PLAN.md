# Part E — Isolated Execution Sandbox & Target-App Templates — Plan

Roadmap ref: §13 Part E, §3.2, §5.1 (HC-14, HC-06, HC-05), §6.6, §19 (R2, R4, R5) · Milestone: M1 · Gate: **G-E** · Working Plan §9 (TS-04…TS-08, TS-11), §20 Part E · Spec §7.2, §9.3, §11.2–11.3, §13.1, §14.1, §18, §22, §25 · Date: 2026-11-23

| Field | Value |
|---|---|
| Status | **Revision 3: final draft for the owner's approval.** Not confirmed. No Part E code written, nothing installed or configured |
| Owner rulings so far | **D-07 = (b) Docker Engine inside WSL2** (AP-019). **Completely free implementation** (AP-019); defined precisely in §12.1 |
| Proposed, awaiting owner approval | D-04, D-08, D-16, D-24, D-45…D-52 and the container limit values (§2, §3.2). None of these is approved yet |
| Preconditions | **G-D approved: not yet** (Part D awaits your review). Plan approval. Feasibility spikes S-1 (mutual TLS) and S-2 (offline React build) pass in E.1/E.2; if either fails, work stops and the blocker comes back to you (§11) |
| Harness components | Implements **HC-14** (sandbox). Extends **HC-06** (execute tools), **HC-05** (Resource Guard, LLM/sandbox lock), HC-21 (audit of every execution), HC-11 (spans) |
| Agents | **None.** No agent behaviour changes. The ten agents stay exactly as in Part D |
| Branch | `part/e-sandbox`, created from `main` **after** G-D is approved and Part D is merged. One local commit per checkpoint E.1–E.5. No push without your separate approval |

**Revision history:**

| Rev. | Date | Change |
|---|---|---|
| 1 | 2026-11-23 | First draft (AP-018) |
| 2 | 2026-11-23 | D-07 ruled (b); free-only requirement; Docker Engine in WSL2 set-up; D-50, D-51 (AP-019) |
| 3 | 2026-11-23 | Review corrections: tiered licence policy (§12.3) replacing the blanket GPL ban; mutual-TLS feasibility spike S-1 with stop rule; PostgreSQL execution deferred to Part K (D-16); precise free-only definition (§12.1, §12.2); offline React dependency strategy and spike S-2 (§3.5); all non-ruled decisions labelled *proposed*; new D-52 (licence of template files) |

---

## 0. Part E in simple words

Parts C and D built the safety walls and the assembly line. The agents can write files, but **nothing the platform generates has ever been run**. Part E builds the **locked room** where generated code is allowed to run, and the two **starter kits** every generated application begins from.

When it is done:

1. **Generated code runs only inside a throw-away Docker container.** The container:
   - has **no network at all**;
   - has strict limits on memory, CPU, number of processes and time;
   - runs as a normal (non-root) user, with a read-only system disk;
   - cannot see your home folder or any platform folder (nothing from the laptop is mounted into it);
   - is deleted after every run; only the logs and results are kept.
2. **There is no "run it on the laptop instead" fallback.** If Docker is missing or stopped, the work is marked **Blocked** with the reason. It never runs unsafely.
3. **The AI and the sandbox never run at the same time.** Before a container starts, the platform asks Ollama to unload its model and checks free memory. If memory is short, the run waits.
4. **Agents cannot type commands.** They can only ask for named, pre-approved actions ("recipes"), such as "run the API tests" or "build the frontend". Each recipe has a fixed command and checked arguments.
5. **Two starter kits** (templates), each with a manifest that says which files agents may fill in and which are protected:
   - `react_fastapi_sqlite`: React + Vite + TypeScript frontend, FastAPI backend, SQLite database;
   - `html_fastapi_sqlite`: plain HTML/CSS/JS frontend, same backend.
6. **Each starter kit proves itself.** Its own tests and build run inside the sandbox, offline, and the result is stored as a *template verification* record. Part G will only call a technology "supported" when that record says it passed.
7. **Everything is free.** Docker Engine runs inside the Ubuntu system already installed in WSL2 on this laptop; Docker Desktop is **not** used. After a one-time set-up that downloads free packages, all sandbox work runs offline. §12 defines "free-only" precisely and lists every component.
8. **What Part E does not do:** no agent writes real application code yet (K, L); real test results for generated projects arrive in M; no repair loop (N); no packaging (O); nothing is published (P); **no PostgreSQL containers** (deferred to K, D-16).

---

## 1. Scope

**In scope:** roadmap §13 Part E and Working Plan §20 Part E, delivered as five internal checkpoints under one gate (the D-32 pattern used in C and D).

| Checkpoint | Working Plan steps | What |
|---|---|---|
| **E.1 Foundations and environment validation** | TS-04 (contracts), TS-07 (detection) | You set up Docker Engine in WSL2 with the runbook and setup script (§3.10). **Spike S-1: mutual-TLS connection** (§3.11), which must pass before anything depends on it. Spike S-3: archive copy-in/copy-out and WSL forwarding/idle behaviour. Docker SDK dependency with licence check. Contracts: `ContainerLimits`, `RecipeSpec`, `SandboxExecution`, `TemplateManifest`, `TemplateVerification`. Migration `0007_sandbox`. Settings section `sandbox`. Import rules. Doctor checks |
| **E.2 Sandbox runner and Resource Guard** | TS-04, TS-07, TS-08 | `ContainerSpec` builder (limits can never be loosened). Runner: create → copy in → start → watchdog → collect → remove (always, in `finally`). Peak-memory sampling. Startup sweep of leftover containers. Resource Guard: cross-process LLM/sandbox lock, Ollama unload, free-RAM check, Docker availability → **Blocked**. Image Dockerfiles, licence policy, trusted build script, digest lock. **Spike S-2: offline React build** in the production container spec (§3.5), which must pass before E.4 |
| **E.3 Recipes and tools** | TS-05, TS-06 | Recipe registry (10 recipes, argument schemas). Tools `request_build`, `request_test_run`, `request_migration_check` (side effect `execute`). JUnit XML and coverage parsing (test runner base). Migration Runner (disposable SQLite only) |
| **E.4 Templates and Integration/Build Service base** | Templates, self-test | Both templates with `template.yaml` manifests and the `req` pytest marker plugin. Integration/Build Service base: copy a template into `generated/`, protect files by checksum, render `.env` from placeholders. Template self-test → `TemplateVerification` records. Worker job, API endpoints and a small UI page to request and view verifications |
| **E.5 Isolation suite, E2E-3 and evidence** | TS-11, E2E-3 | `docker` isolation suite with negative controls. E2E-3 (template → integration service → sandbox → results via API). Peak memory under `low_memory` (AC-E-03). Docs, runbook, trackers, completion report |

**Out of scope:**

| Excluded | Comes in |
|---|---|
| Agents generating real application code; any change to agent behaviour | K, L |
| Wiring the run graph's `integration_build` and `test_runner` nodes to the sandbox. They stay honest stand-ins in E (D-47) | K/L (build), M (tests) |
| **PostgreSQL execution**: no PostgreSQL image, container, credentials or flag in E (D-16) | K |
| `test_runs` table, requirement evidence, trivial-test detector | M |
| `apply_patch` tool, repair loop, Completion Gate | N |
| Building the generated app's own `Dockerfile` / `docker-compose` (needs network; delivered as files only) | O (packaging notes) |
| Browser (Playwright) tests | L/N (D-09) |
| Capability Catalog and feasibility (they **consume** `TemplateVerification`) | G |
| `local_password_rbac` template variant (only if D-04 (b) is ever chosen) | K |
| Any network access from a sandbox, or an "approved endpoints" allowlist (Spec §11.3) | Not in MVP unless separately approved |

---

## 2. Decisions

### 2.1 Summary

| ID | Decision | Proposed choice | Status |
|---|---|---|---|
| D-07 | Docker distribution | (b) Docker Engine inside WSL2 (`Ubuntu-24.04`); Docker Desktop not used | **Ruled (AP-019)** |
| D-04 | Authentication in generated apps | No authentication in Part E | Proposed, awaiting owner approval |
| D-08 | Dependencies and audits | Prebuilt images with locked dependencies; offline execution | Proposed, awaiting owner approval |
| D-16 | PostgreSQL in Docker | Disabled; **execution deferred to Part K**; E contains no PostgreSQL code path | Proposed, awaiting owner approval |
| D-24 | Integration/Build Service | Deterministic service, not an agent | Proposed, awaiting owner approval |
| D-45 | Docker/WSL memory cap | Owner-managed `.wslconfig`, 4 GB | Proposed, awaiting owner approval |
| D-46 | Cross-process LLM/sandbox lock | SQLite resource lease | Proposed, awaiting owner approval |
| D-47 | Sandbox in the run graph | No graph wiring in Part E | Proposed, awaiting owner approval |
| D-48 | Files into and out of containers | Docker archive API copy-in/copy-out; no host bind mounts | Proposed, awaiting owner approval |
| D-49 | Who starts template verification | Manual request through UI or script | Proposed, awaiting owner approval |
| D-50 | Windows → engine connection | Mutual TLS on loopback, **subject to spike S-1 passing** | Proposed, awaiting owner approval |
| D-51 | Docker Engine source | Ubuntu archive `docker.io` | Proposed, awaiting owner approval |
| D-52 | Licence of template files delivered in generated apps | Decide together with D-21 before Part P; E ships no `LICENSE` file in templates | Proposed, awaiting owner approval |

### 2.2 Details: roadmap decisions due in Part E

| ID | Options | Proposed choice and reason |
|---|---|---|
| **D-07** | (a) Docker Desktop; (b) Docker Engine inside WSL2; (c) Podman | **Ruled (AP-019): (b)**, in the existing `Ubuntu-24.04` distribution (systemd already enabled). Docker Desktop stays installed but is **not used**: the platform refuses its connection and refuses an engine that reports itself as Docker Desktop. ADR-0007 records the ruling |
| **D-04** | (a) `no_auth` only; (b) also `local_password_rbac` | **(a) No authentication in Part E.** The manifest reserves an `auth` slot so (b) can be added later as a template extension with its own verification. (b) now would add template scope, security tests and a password-hashing dependency to E |
| **D-08** | (a) Prebuilt images with locked dependencies, offline execution; (b) disclose audits unavailable | **(a).** Dependencies are installed **only when the trusted image-build script runs** (network at build time only). The same script runs `pip-audit` and `npm audit` against the locked dependencies; if the advisory databases cannot be reached, the audit is recorded as **"not run"** (never as passed) and the image is still usable only after you acknowledge it. Inside the sandbox there is never network. A template that needs a package not in its image's lock is refused, not fetched |
| **D-16** | Include behind a flag / defer | **Defer PostgreSQL execution to Part K.** Part E adds no PostgreSQL image, container, credentials handling or configuration flag. The Migration Runner's request contract has an `engine` field that accepts only `sqlite` in E; a `postgresql` request is refused with "not available until Part K (D-16)", never silently replaced by SQLite. Reason: the roadmap allowed "behind a flag", but implementing and testing a second database container on a 16 GB laptop adds memory risk and an extra image for a feature no part needs before K. This is a deviation from roadmap §13 E wording and will be recorded as a DV row on approval |
| **D-24** | — | **Deterministic service, not an agent** (principal `service:integration_builder`, already declared in Part C IAM) |

### 2.3 Details: new decisions for Part E

| ID | Question | Options | Proposed choice and reason |
|---|---|---|---|
| **D-45** | Docker/WSL memory cap (Spec §25: configuration) | (a) You set `%USERPROFILE%\.wslconfig` (`memory=4GB`, `processors=2`, `swap=2GB`, `autoMemoryReclaim=gradual`) from the runbook; the doctor warns if it is missing or larger. (b) The platform writes `.wslconfig` | **(a).** No `.wslconfig` exists today; the WSL VM currently sees 7.8 GB (checked read-only 2026-11-23). With Docker Engine inside WSL this cap is the ceiling for all containers together. 4 GB leaves room for Ollama on Windows (≈2.1 GB for a 3B model, unloaded before any sandbox run anyway), the API, the worker and a browser. The platform never edits files in your home folder |
| **D-46** | The LLM/sandbox lock must work across processes; today `MODEL_LOCK` is a thread lock inside one process | (a) One SQLite resource lease (`resource_lock` row, like the D.4 worker lease) taken by both the gateway and the sandbox, with heartbeat and stale takeover. (b) An OS file lock in `data/` | **(a).** Proven pattern, visible in the database, recoverable after a crash. `MODEL_LOCK` stays as an inner guard |
| **D-47** | Should E connect the sandbox to the run graph? | (a) No; nodes stay honest stand-ins; the sandbox is reached through verification jobs and the new tools (which no agent uses until K). (b) Build the template during every run | **(a).** No agent writes code yet, so building in a run would only rebuild an empty starter kit and could be mistaken for building the project |
| **D-48** | How files get into and out of a container | (a) Docker archive API copy-in/copy-out into a per-execution anonymous volume; no host bind mounts. (b) Bind-mount a disposable staging copy | **(a).** No host path is ever visible to the container, and Windows folders never have to be shared into the WSL engine. Verified by spike S-3. If S-3 fails, work stops and the blocker comes to you; (b) is **not** adopted without your approval |
| **D-49** | Who starts template verification | (a) You, from the UI or `scripts/verify_templates.py`; queued and run by the worker (D-41). (b) Automatically at start | **(a)**, plus an automatic "stale" flag when a template's or image's checksum changes |
| **D-50** | How Windows reaches the engine inside WSL2 | (a) **Mutual TLS on loopback** `127.0.0.1:2376`, private CA for this laptop. (b) Plain TCP, no authentication. (c) SSH transport. (d) Move the worker into WSL | **(a), subject to spike S-1.** (b) is refused: any local program, or a web page through the browser, could drive the engine with root power inside WSL. (c) needs `paramiko` (LGPL) or running `ssh.exe` from the harness. (d) changes the approved architecture (D-41). If S-1 fails, the blocker and options come back to you (§3.11); TLS is never weakened and the key is never stored in plain text as a workaround |
| **D-51** | Docker Engine source | (a) Ubuntu archive `docker.io` + `docker-buildx`. (b) Docker's apt repository `docker-ce` | **(a).** Free, Apache-2.0, updated through normal Ubuntu updates, no third-party repository or signing key. (b) is also free; used only with your approval if Ubuntu's version lacks a needed feature |
| **D-52** | Licence of the template files that end up inside generated applications (our own code, e.g. the app skeleton and the `req` plugin) | (a) Decide together with the platform licence D-21, before Part P publishes anything. (b) Decide now | **(a).** Nothing is published before Part P. E ships no `LICENSE` file in the templates; the template README says the licence is not yet chosen. Third-party code is never copied into templates (only referenced as dependencies) |

---

## 3. Architecture

### 3.1 Package layout and dependency rules

| Code | Package | Notes |
|---|---|---|
| Sandbox runner, `ContainerSpec` builder, recipe registry, Resource Guard, execute tools | `devteam_harness.sandbox` (new): `spec.py`, `engine.py` (the **only** module that imports `docker`), `tls.py` (mutual-TLS context, §3.11), `runner.py`, `recipes.py`, `guard.py`, `results.py` (JUnit/coverage parsing), `tools.py` | Harness = HC-14 |
| Ollama "unload model" and "what is loaded" calls | `devteam_harness.llm_gateway.providers.ollama` (`keep_alive: 0`, `/api/ps`) | HTTP stays in providers (import contract 7) |
| Integration/Build Service base, Migration Runner, Test Runner base, template self-test | `devteam_services.{integration_builder, migration_runner, test_runner, templates}` | Deterministic services; reach the sandbox only through the harness |
| Template verification job | `devteam_worker` (new job kind) | The worker stays the only executor |
| Verification request and read endpoints | `devteam_api` (`routers/sandbox.py`) | API writes a request row; never touches Docker |
| Templates | `templates/target_apps/{react_fastapi_sqlite,html_fastapi_sqlite}/` | Data, not platform code: excluded from platform ruff, mypy, pytest collection and the npm workspace |
| Images and licence policy | `sandbox/images/{python_api,node_web}/`, `sandbox/images/licence_policy.yaml`, `sandbox/images/images.lock.json` | Built only by `scripts/build_sandbox_images.py` |

**New import-linter contracts** (each with a planted-violation test, as in C and D):

1. Only `devteam_harness.sandbox.engine` may import `docker`.
2. Agents, orchestration, the API and the worker may not import `devteam_harness.sandbox.engine` (they go through tools, services or the job interface).
3. The API may not import the execution functions of `devteam_services.{integration_builder,migration_runner,test_runner,templates}` (read-only queries only).

**AC-E-04 static rule (no host execution):** a test scans every non-test module in `packages/`, `apps/` and `agents/` and fails on `subprocess`, `os.system`, `os.exec*`, `os.spawn*`, `os.startfile`, `asyncio.create_subprocess_*` and `pty`. Allowlist: none. Today only tests and `scripts/` use `subprocess` (checked 2026-11-23). `agents/ruff.toml` also gets the `docker` ban that `CLAUDE.md` already lists.

### 3.2 Container limits (values proposed for confirmation; unchanged from revision 2)

Every container is built by one function. A recipe may ask for *less* memory or time, never more than the profile ceiling. Limits are not raised without measured evidence and your approval.

| Control | `low_memory` (default) | `standard` | Fixed (not configurable) |
|---|---|---|---|
| Network | — | — | `network_mode="none"` |
| Memory (hard, no extra swap) | 1,536 MB | 2,048 MB | `memswap_limit = mem_limit` |
| CPUs | 1.5 | 2.0 | — |
| Processes | 256 | 256 | `pids_limit` always set |
| Wall clock (ceiling) | 600 s | 900 s | Watchdog kills, then removes |
| `/tmp` tmpfs | 256 MB, `noexec,nosuid` | 256 MB | — |
| Work area `/work` | per-execution anonymous volume, 512 MB copy-in cap | same | Removed with the container |
| Output kept per stream | 1 MB (head and tail kept, middle cut and marked) | same | — |
| User | — | — | `10001:10001`, never root |
| Root filesystem | — | — | `read_only=True` (includes the preinstalled dependencies, §3.5) |
| Privileges | — | — | `cap_drop=["ALL"]`, `no-new-privileges`, not privileged, no devices, `ipc_mode="private"`, no host PID/IPC/UTS |
| Mounts | — | — | **None from the host** (D-48). Never the Docker socket |
| Environment | — | — | Only what the recipe declares (§3.4). Host environment is never passed through |
| Labels | — | — | `devteam.sandbox=1`, `devteam.execution_id`, `devteam.project_id` (for the startup sweep) |

Settings: new `sandbox` section in `config/defaults.yaml` and profiles (validated, unknown keys rejected): `enabled`, `docker_host` (default `tcp://127.0.0.1:2376`; only loopback with mutual TLS is accepted, D-50), `tls_dir` (default `data/sandbox_tls`), `wsl_distribution` (default `Ubuntu-24.04`, used by the doctor and `dev.ps1` only), the limits above, `min_free_ram_mb` (2,048 on `low_memory`), `unload_models_before_run: true`, `image_lock_path`. There is **no** PostgreSQL setting (D-16).

### 3.3 Execution flow (one sandbox run)

1. **Request.** A tool call (`request_test_run`, …) or a service asks for recipe *R* with arguments *A* on project *P*. Arguments are validated against *R*'s schema; unknown recipes and extra arguments are refused.
2. **Policy.** The Tool Layer checks role grant, stage and approved spec (Part C engine). Execute tools are allowed only in stages `build`, `delivery` and `change_request`, which already require an approved, frozen spec.
3. **Resource Guard.**
   1. Docker check: the SDK reaches the engine over mutual TLS (`ping`); the engine is Linux and is **not** Docker Desktop; the image exists **and its digest matches `images.lock.json`**. If not → **Blocked** with a specific reason (`sandbox.docker_unavailable`, `sandbox.tls_not_configured`, `sandbox.engine_not_allowed`, `sandbox.image_missing`, `sandbox.image_digest_mismatch`). Never a host fallback.
   2. Take the cross-process resource lease (D-46). If an LLM call holds it → wait.
   3. Ask Ollama to unload loaded models and confirm with `/api/ps`. If Ollama is not running, nothing is loaded: continue.
   4. Free RAM ≥ `sandbox.min_free_ram_mb` → continue; otherwise release the lease and return **waiting for resources** (the existing Part D mechanism).
4. **Run.**
   1. Create the container (§3.2) without starting it; `/work` is a fresh anonymous volume.
   2. Copy in a tar of the allowed files only: the workspace's `generated/` tree, filtered (no symlinks, nothing outside the tree, size cap, no `node_modules/`, no `.env` other than the rendered placeholder one).
   3. Start. Sample memory from the stats stream every second (peak recorded).
   4. Watchdog: kill at the wall-clock ceiling.
5. **Collect.** Exit code, OOM flag, capped stdout/stderr, and declared result files (`/work/.devteam/out/junit.xml`, `coverage.xml`) copied out → `workspaces/<P>/runs/<run_or_verification_id>/sandbox/<execution_id>/`.
6. **Remove.** Container and its volume removed in `finally`, including after errors and cancellation. The worker's startup sweep removes any `devteam.sandbox=1` container left by a crash.
7. **Record.** One `sandbox_executions` row, audit event `sandbox.executed`, a trace span (no file content or output text in audit, traces or logs). Lease released.

### 3.4 Recipes (TS-05)

All commands are fixed argument lists (never a shell string, never `npm`/`npx`/`pip` at run time). Arguments are only enum values or validated relative paths inside `/work`. Every recipe sets: `HOME=/work/.devteam/home`, `PYTHONDONTWRITEBYTECODE=1`, `CI=1`, `NO_COLOR=1`; Node recipes also `NODE_ENV=production` (build) or `test` (tests); Python recipes `COVERAGE_FILE=/work/.devteam/.coverage`.

| Recipe | Image | Command (fixed argv) | Arguments allowed | Outputs | Tool |
|---|---|---|---|---|---|
| `pytest_api` | `python_api` | `python -m pytest -p devteam_req -p no:cacheprovider -m "not db" --junitxml=/work/.devteam/out/junit.xml --cov=app --cov-report=xml:/work/.devteam/out/coverage.xml tests` (in `/work/backend`) | `select`: marker enum (`api`, `smoke`, all) | JUnit, coverage | `request_test_run` |
| `pytest_db` | `python_api` | same with `-m db`; `DATABASE_URL=sqlite:////work/.devteam/test.db` | — | JUnit, coverage | `request_test_run` |
| `alembic_upgrade` | `python_api` | `python -m alembic upgrade head`, `downgrade base`, `upgrade head` on a disposable SQLite file in `/work/.devteam/` | — | log, final revision | `request_migration_check` |
| `alembic_autogenerate` | `python_api` | `python -m alembic revision --autogenerate -m <slug>` on a disposable DB | `message`: slug `[a-z0-9_]{1,40}` | new revision file, returned as a proposed diff (the sandbox never writes into `generated/`) | `request_migration_check` |
| `ruff_check` | `python_api` | `python -m ruff check app tests --output-format json --no-cache` | — | findings | `request_build` |
| `bandit_scan` | `python_api` | `python -m bandit -r app -f json` | — | findings | `request_build` |
| `static_validation` | `python_api` | `python -m devteam_static_check frontend` (HTML parse, broken local links/scripts, no external URLs) | — | findings | `request_build` |
| `tsc_check` | `node_web` | `node /node_modules/typescript/bin/tsc --noEmit -p tsconfig.json` (in `/work/frontend`) | — | diagnostics | `request_build` |
| `vitest_run` | `node_web` | `node /node_modules/vitest/vitest.mjs run --reporter=junit --outputFile=/work/.devteam/out/junit.xml` | — | JUnit | `request_test_run` |
| `npm_build` (name kept from the spec's catalog; does **not** run npm) | `node_web` | `node /node_modules/vite/bin/vite.js build --outDir /work/.devteam/out/dist --emptyOutDir` | — | log, `dist/` file list | `request_build` |

Tool grants follow `docs/architecture/HARNESS_ASSESSMENT.md` (keys B/T/M): Database Developer M; Backend B, T; Frontend B; Tester T; Debugger B, T. Added to `config/guardrails/policies.yaml`, `KNOWN_TOOLS` and `STAGE_CEILINGS`. The Part C guard "execute tools arrive with the sandbox (E)" is lifted **for `execute` only**; `external` stays refused until Part P. **No agent calls these tools in Part E**; they are tested directly through the Tool Layer with real Docker.

Test results: the test runner base turns JUnit into counts and per-test outcomes, stored on the `sandbox_executions` row and (for self-tests) in `TemplateVerification`. `TestRun` records and their table arrive in Part M.

### 3.5 Images and offline dependency strategy (TS-08, D-08)

| Image | Base (pinned by digest) | Preinstalled, from a committed lock |
|---|---|---|
| `devteam-sandbox/python_api` | `python:3.12-slim` (Debian) | Into the system `site-packages` (read-only at run time): fastapi, uvicorn, pydantic, sqlalchemy, alembic, httpx, pytest, pytest-cov, ruff, bandit, plus our `devteam_req` plugin and `devteam_static_check`. Exact versions with hashes in `sandbox/images/python_api/requirements.lock`, installed with `pip install --require-hashes --no-deps` |
| `devteam-sandbox/node_web` | `node:22-slim` (Debian) | Into **`/node_modules`** at the image root (root-owned, read-only at run time): react, react-dom, vite, @vitejs/plugin-react, typescript, vitest, jsdom, @types/react, @types/react-dom, with their exact dependency tree from `sandbox/images/node_web/package-lock.json`, installed with `npm ci --ignore-scripts` |

**How React dependencies resolve offline (no symlinks, no download, no writes to the image):**

1. **One lock, made for Linux.** `sandbox/images/node_web/package-lock.json` is generated **inside** the `node:22-slim` build container (`npm install --package-lock-only`), so it contains the Linux x64 glibc builds of native packages (esbuild, Rollup). A lock made on Windows would miss them.
2. **Template matches the lock.** The React template's `frontend/package.json` lists exactly the same package names and versions. A contract test fails if the template asks for anything that is not in the image lock, or at a different version. The template has **no** `node_modules`, and copy-in refuses one.
3. **Resolution by Node's standard parent-folder lookup.** The frontend is copied to `/work/frontend` (the per-execution volume, mounted at `/work`). Node looks for packages in `node_modules` folders of the current folder and **every parent folder**: `/work/frontend/node_modules` (absent), `/work/node_modules` (absent), then **`/node_modules`** in the read-only image. This is the default algorithm for both CommonJS `require` and ES-module `import`, so it covers the Vite config (`@vitejs/plugin-react`), Vite's own resolver, Vitest and the built bundle. TypeScript (`moduleResolution: "bundler"`) and its automatic `@types` lookup walk parent folders the same way. No `NODE_PATH`, no path aliases, no symlinks, no copying of packages per run. (`NODE_PATH` was rejected because it does not apply to ES-module imports.)
4. **No package manager at run time.** Recipes call `node …/vite.js`, `…/tsc`, `…/vitest.mjs` directly (§3.4). `npm` and `npx` are never run in a sandbox, so nothing can try to install. The network is also absent (`network_mode=none`).
5. **Writes go only to `/work` and `/tmp`.** Build output goes to `/work/.devteam/out/dist`; Vite's cache goes to `/work/.devteam/vite-cache` (`cacheDir` in the template config); Vite's temporary bundled config file is written next to `vite.config.ts` in `/work/frontend`. The image's `/node_modules` and the rest of the root filesystem are read-only.

The Python image follows the same rule: packages in the read-only system `site-packages`, nothing installed at run time, caches redirected to `/work` (`-p no:cacheprovider`, `--no-cache`, `COVERAGE_FILE`, `PYTHONDONTWRITEBYTECODE`).

**Spike S-2 (E.2, before templates are finalised in E.4): offline React build.** In a container created by the **production** spec builder (read-only root, `network_mode=none`, non-root, no host mounts, `cap_drop ALL`), with the React template copied in:

| # | Check | How it is proven |
|---|---|---|
| 1 | The React template builds offline | `npm_build` recipe exits 0; `dist/index.html` and a JS bundle are produced; `tsc_check` and `vitest_run` also pass |
| 2 | Dependencies come from the image | Each recipe prints `require.resolve` for `react`, `vite`, `@vitejs/plugin-react`; every path starts with `/node_modules/`; no `node_modules` folder exists under `/work` |
| 3 | No download is attempted | The container has only the loopback interface (checked inside); `npm`/`npx` are not called; a probe in the same container confirms that outbound TCP and DNS fail; build logs contain no registry URL |
| 4 | The trusted image is not modified | Image digest identical before and after; `docker diff` on the stopped container shows **no** changes (read-only root; `/work` is a volume) |
| 5 | Restrictions stay effective | In the same container: writing to `/node_modules` and `/usr` fails; running as UID 10001 confirmed; executing a file from `/tmp` fails (`noexec`); memory and PID limits present in `docker inspect` |

**Stop rule:** if S-2 cannot pass without loosening a restriction (for example a writable root or network), work stops and the blocker comes back to you with options. Restrictions are not relaxed to make the build pass.

**Image building and supply chain:**

- Built only by `scripts/build_sandbox_images.py`, run by you once and whenever a lock changes. It is the **only** step with network access (pulls from Docker Hub, PyPI and the npm registry, §12.2).
- Base images are pinned by digest. Python packages are pinned with hashes; npm packages by `package-lock.json` integrity hashes.
- The script records each image's digest, the licence report (§12.3) and the audit result in `sandbox/images/images.lock.json` (committed).
- At run time an image whose digest does not match the lock is refused.
- Estimated download: ≈150 MB (python slim) + ≈80 MB (node slim) + packages; disk ≈1.5 GB.

### 3.6 Templates

Location: `templates/target_apps/<template_id>/`. Both share the backend; they differ in the frontend.

| Part | Contents |
|---|---|
| Backend `backend/` | FastAPI app factory; `DATABASE_URL` from environment (SQLite default `sqlite:///./data/app.db`); SQLAlchemy 2 models base; Alembic with one baseline revision; `/health` and `/ready`; standard error format (`{"error": {"code", "message", "request_id"}}`); JSON logging; `tests/` with API and DB smoke tests using the `req` marker |
| `req` marker plugin | `@pytest.mark.req("FR-001", "BR-002")`; writes requirement IDs into JUnit properties so Part M can map tests to requirements. Its own tests are part of the self-test |
| Frontend (`react_fastapi_sqlite`) | Vite + React + TS strict; `package.json` matching the image lock exactly (§3.5); `tsconfig.json` (`moduleResolution: "bundler"`) and `vite.config.ts` (`cacheDir` in `/work/.devteam`); a typed API client folder (filled in K/L); one health-status page; Vitest smoke test |
| Frontend (`html_fastapi_sqlite`) | `index.html`, `styles.css`, `app.js` calling `/health`; checked by `static_validation` |
| Delivery files | `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.env.example` (placeholders only), `README.md.tmpl`, `.gitignore`. Delivered as files only, **not** built or run by the platform (they need network). No `LICENSE` file yet (D-52) |
| `template.yaml` | `id`, `version`, `checksum` (computed), `writable_slots` (globs agents may fill, e.g. `backend/app/models/**`, `backend/app/api/**`, `frontend/src/pages/**`, `backend/tests/**` for the Tester only), `protected_files` (e.g. `backend/app/main.py`, `alembic/env.py`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `.github/**`, the plugin), `recipes`, `image` per recipe, `dependencies` (must equal entries in the image lock), `env_placeholders`, `auth: none` (D-04), `database: sqlite` (D-16) |

**Integration/Build Service base:** materialise a template into `workspaces/<P>/generated/` only if it is empty; record checksums of protected files (any later change is reported as a violation in K/L); render `.env` from `env_placeholders` (non-secret values only, e.g. the SQLite path; anything secret-like stays a placeholder). It does **not** build in a run in E (D-47).

**Template self-test:** materialise the template into a temporary verification workspace, then run its recipes in the sandbox (backend: `ruff_check`, `alembic_upgrade`, `pytest_api`, `pytest_db`; react: also `tsc_check`, `vitest_run`, `npm_build`; html: `static_validation`). Result: one `TemplateVerification` record, status `passed` / `failed` / `blocked`, with per-recipe outcome, template checksum, image digests, platform version, duration and peak memory. A changed template or image makes earlier records `stale`.

### 3.7 Contracts added (C.1 rules: version + JSON schema snapshot)

| Contract | Key fields |
|---|---|
| `ContainerLimits` v1 | memory, cpus, pids, wall clock, tmpfs size, output cap |
| `RecipeSpec` v1 | id, image, argv template, argument model name, environment, timeout, outputs, tool |
| `SandboxExecution` v1 | `sbx_` ID, project, run or verification ID, recipe, image digest, engine version, limits applied, status (`completed` / `failed` / `timed_out` / `oom_killed` / `blocked` / `cancelled`), exit code, duration, peak memory, test counts, output refs, reason |
| `TemplateManifest` v1 | as `template.yaml` above |
| `TemplateVerification` v1 | `tvf_` ID, template ID, version, checksum, status (`queued` / `running` / `passed` / `failed` / `blocked` / `stale`), per-recipe results, image digests, duration, peak memory, requested by, timestamps |
| `MigrationCheckRequest` v1 | recipe, `engine: Literal["sqlite"]` (D-16) |

New ID prefixes `sbx` and `tvf`; events `sandbox.executed`, `template.verification_recorded`; audit actions `sandbox.executed`, `sandbox.blocked`, `template.verified`. Expected schema count: 92 → about 100.

### 3.8 Data: migration `0007_sandbox`

| Table | Purpose |
|---|---|
| `sandbox_executions` | One row per container run (the `SandboxExecution` contract). Append-only by trigger, like `agent_outputs` |
| `template_verifications` | Requests and results. Status changes only forward (`queued → running → passed/failed/blocked`, any → `stale`) |
| `resource_lock` | Single row: holder (`llm` / `sandbox`), owner ID, heartbeat (D-46) |

Every domain row carries `project_id` (verifications use a reserved platform project ID, as the audit log does for platform events). Upgrade and downgrade tested; the existing database is backed up automatically before migration (Part A behaviour); rehearsal on a read-only copy of `data/state.db` as in Part D.

### 3.9 API and UI

| Endpoint | Purpose |
|---|---|
| `GET /api/sandbox/status` | Engine reachable, engine version, images present and digests match, WSL cap check, lock holder |
| `GET /api/sandbox/templates` | Templates with their latest verification |
| `POST /api/sandbox/templates/{template_id}/verifications` | Queue a verification (rate-limited, audited); the worker runs it |
| `GET /api/sandbox/verifications/{id}` | Result, per-recipe outcome, peak memory, log references |

UI: a small **Sandbox** page (linked from the header): engine status, the two templates, "Verify" button, results. OpenAPI and web types regenerated. Under D-15, platform UI is checked by API E2E plus a manual demo.

### 3.10 Docker Engine inside WSL2 (D-07 (b), D-50, D-51)

Current state (checked read-only, 2026-11-23): WSL 2.7.11; `Ubuntu-24.04` with **systemd enabled**; OpenSSL present; Docker Engine **not installed**; no `.wslconfig`.

**One-time set-up, done by you in E.1** (it needs your Ubuntu `sudo` password, which the platform never sees or stores). `docs/runbooks/docker-setup.md` gives every command; `scripts/setup_wsl_docker.ps1` runs the same steps and stops at the first error.

1. Optional but recommended: set `.wslconfig` (D-45), then `wsl --shutdown`.
2. Inside Ubuntu: `sudo apt install docker.io docker-buildx` (D-51); `sudo systemctl enable --now docker`.
3. Certificates (OpenSSL inside Ubuntu):
   - a private CA, valid 2 years;
   - a server certificate for `IP:127.0.0.1` and `DNS:localhost`; its key stays in `/etc/docker/tls/` (root only, mode 600);
   - a client certificate whose key is written **only in encrypted form** (PKCS#8, AES-256). The setup script creates a random passphrase on Windows, stores it in Windows Credential Manager (entry `devteam-ai`, through the existing Part C secret store), and passes it to OpenSSL through standard input (never on a command line, never in a file);
   - the CA key is deleted.
4. The engine listens on its normal Unix socket **and** on `tcp://127.0.0.1:2376` with `tlsverify` (systemd override plus `/etc/docker/daemon.json`).
5. Client certificate, encrypted client key and CA certificate are copied to `data/sandbox_tls/` (git-ignored, like all of `data/`).
6. `uv run python scripts/doctor.py` confirms everything; `uv run python scripts/build_sandbox_images.py` builds the two images (§3.5).

**Day-to-day:** `scripts/dev.ps1` starts the Ubuntu distribution and keeps one idle session open while the platform runs, because WSL stops an idle distribution (verified in spike S-3). If the engine is not reachable, sandbox work is **Blocked** with the reason.

**Platform code** never runs `wsl.exe`, `docker.exe` or any other program (AC-E-04). Only the owner-run scripts (`setup_wsl_docker.ps1`, `dev.ps1`, `build_sandbox_images.py`) call external programs, as `scripts/` already does today.

**Docker Desktop:** not needed and not used. If its WSL integration is enabled for `Ubuntu-24.04`, the runbook explains how to turn it off so the two engines do not clash. Uninstalling it is your choice.

### 3.11 Mutual-TLS client design and spike S-1 (D-50)

**Intended integration.** The Docker SDK's own `TLSConfig` takes file paths for the client certificate and key and has no passphrase option, so it cannot load an encrypted key. The plan therefore does not use `TLSConfig` for the client certificate. Instead `sandbox/tls.py`:

1. reads the passphrase from Windows Credential Manager through the Part C secret store (`SecretReference` `sandbox.docker_client_key`), never from configuration or environment;
2. builds a standard-library `ssl.SSLContext` with `PROTOCOL_TLS_CLIENT` (certificate and host-name verification **on**, TLS 1.2 minimum), loads the CA certificate as the only trust anchor, and calls `load_cert_chain(certfile, keyfile, password=<callable>)`. OpenSSL decrypts the key **in memory**;
3. creates the SDK's low-level `APIClient` for `https://127.0.0.1:2376` with an explicit API version (so the SDK makes no request before step 4), then mounts a small `requests` transport adapter that hands this `SSLContext` to urllib3 for every connection;
4. only then makes the first request (`ping`, `version`).

The passphrase is held only for the duration of `load_cert_chain` and never logged; it is registered with the Part C redactor.

**Spike S-1 (E.1, before any other sandbox code depends on the connection):**

| # | Requirement | Pass condition |
|---|---|---|
| 1 | Engine reachable through the Windows loopback endpoint | From Windows Python, TCP connect to `127.0.0.1:2376` succeeds; the engine is the WSL one (its `version` reports Ubuntu's `docker.io`, not Docker Desktop) |
| 2 | Mutual TLS works | `ping` and `version` succeed with the client certificate. **Negative controls:** without a client certificate the engine rejects the handshake; with a certificate from a different CA the engine rejects it; with a server certificate the client does not trust, the client refuses |
| 3 | Encrypted key loads through the intended SDK integration | The client key file starts with `BEGIN ENCRYPTED PRIVATE KEY`; the adapter from steps 1–4 above connects with it; a wrong passphrase fails |
| 4 | Passphrase comes securely from Credential Manager | Retrieved through the Part C secret store only; absent from configuration, environment, logs, audit and traces (canary scan as in Part D) |
| 5 | Decrypted key never written to disk | No file containing `BEGIN PRIVATE KEY` / `BEGIN RSA PRIVATE KEY` / `BEGIN EC PRIVATE KEY` exists in `data/`, the repository, `%TEMP%` or the user profile's temp folders after connecting (before/after listing compared; new files scanned) |
| 6 | Certificate verification stays enabled | The context has `verify_mode == CERT_REQUIRED` and `check_hostname == True`; a test fails if either is changed; no `verify=False`, `CERT_NONE` or `assert_hostname=False` anywhere in the code (static check) |

**Stop rule:** if S-1 cannot meet all six requirements with the SDK, work stops and the blocker comes back to you with the evidence and options. TLS is not weakened, verification is not disabled, and the key is never stored unencrypted as a workaround. The spike result is recorded in ADR-0008.

### 3.12 Spike S-3: file transfer and WSL behaviour (D-48)

| # | Check | Pass condition |
|---|---|---|
| 1 | Copy-in | A tar is copied through the archive API into a created (not yet started) container's `/work` volume while the root filesystem is read-only; files arrive with the expected owner (UID 10001) and permissions |
| 2 | Copy-out | Result files under `/work/.devteam/out` are copied out after the container stops; nothing outside that folder is read |
| 3 | No host mount | `docker inspect` lists no bind mounts; only the anonymous `/work` volume and `/tmp` tmpfs |
| 4 | Cleanup | Removing the container with its volume leaves no volume behind (`docker volume ls` filtered by label) |
| 5 | WSL forwarding and idle | The engine stays reachable from Windows for 30 minutes with `dev.ps1`'s idle session open, and is reported unreachable (Blocked) after `wsl --shutdown` |

**Stop rule:** as for S-1. A bind mount (D-48 option (b)) is not adopted without your approval.

---

## 4. Gaps found in completed parts (reported, not silently fixed)

| Gap | Where | Proposal in E |
|---|---|---|
| `MODEL_LOCK` is per process; Spec §7.2 needs "never LLM and Docker together" across processes | `llm_gateway/gateway.py:42` | D-46 resource lease; the gateway takes it around each call |
| `CLAUDE.md` bans `docker` imports in agents, but `agents/ruff.toml` has no `docker` entry | `agents/ruff.toml` | Add the ban (import-linter rule 5 already forbids non-allowlisted imports, so no current exposure) |
| Doctor checks Docker but not WSL, the engine connection or images | `scripts/doctor.py:137-150` | Add these checks |
| `never_writable` includes `runs/`; sandbox evidence must be written there by the harness (not agents) | `config/guardrails/protected_paths.yaml` | Unchanged for agents; the sandbox writes through the harness evidence writer, as the executor already does for `runs/<run_id>/diffs` |
| The roadmap says "image cache (HC-18)", but HC-18 is rate limiting | roadmap §13 E | Wording slip: the image "cache" is the locally built, digest-locked images; rate limiting applies to the verification endpoint |
| Roadmap §13 E puts PostgreSQL (behind a flag) in E | roadmap §13 E, D-16 | Proposed deferral to K (D-16); recorded as a deviation on approval |

---

## 5. Files

| Area | Create | Modify |
|---|---|---|
| Harness | `packages/harness/src/devteam_harness/sandbox/{__init__,spec,engine,tls,runner,recipes,guard,results,tools}.py` | `tools/registry.py` (allow `execute`), `tools/builtin.py` (register 3 tools), `guardrails/config.py` (`KNOWN_TOOLS`, `STAGE_CEILINGS`), `llm_gateway/gateway.py` (take resource lease), `llm_gateway/providers/ollama.py` (unload, `/api/ps`), harness `pyproject.toml` (`docker`) |
| Services | `packages/services/src/devteam_services/{integration_builder,migration_runner,test_runner,templates}/` | — |
| Core | contracts `sandbox.py`; ID prefixes; events; settings `SandboxSettings` | `contracts/__init__.py`, `config/settings.py`, `contracts/schemas/*` (generated) |
| State | migration `0007_sandbox`; models; repository functions | head-revision test expectations (2 tests, as in D: DV row) |
| Worker | verification job; startup container sweep | `worker.py` |
| API | `routers/sandbox.py`, schemas | `main.py` (router), `openapi.json`; security route-inventory test (new routes) |
| Web | `SandboxPage.tsx`, test | `App.tsx`, `client.ts`, `schema.d.ts` (generated) |
| Templates | `templates/target_apps/react_fastapi_sqlite/**`, `templates/target_apps/html_fastapi_sqlite/**` | — |
| Images | `sandbox/images/python_api/{Dockerfile,requirements.lock}`, `sandbox/images/node_web/{Dockerfile,package.json,package-lock.json}`, `sandbox/images/licence_policy.yaml`, `sandbox/images/images.lock.json`, `sandbox/tools/{devteam_static_check,devteam_req,licence_report}/` | — |
| Config | `config/profiles/*` sandbox values | `config/defaults.yaml`, `config/guardrails/policies.yaml` |
| Scripts | `scripts/setup_wsl_docker.ps1` (run by you once), `scripts/build_sandbox_images.py`, `scripts/verify_templates.py` | `scripts/doctor.py` (WSL distribution, engine over TLS, not Docker Desktop, `.wslconfig` cap, images, certificate expiry), `scripts/dev.ps1` (start and keep the WSL distribution open; status line) |
| Repo | — | `.dockerignore`, root `pyproject.toml` (import contracts, ruff/mypy/pytest excludes for `templates/` and `sandbox/`), `agents/ruff.toml`, `.github/workflows/ci.yml` (still deselects `docker`) |
| Docs | `docs/architecture/sandbox.md`, `docs/runbooks/docker-setup.md`, `docs/adr/0008-sandbox-engine-connection.md` (D-48, D-50, D-51, spike results) | ADR-0007 (ruling recorded 2026-11-23), `.gitignore` (confirm `data/` covers `sandbox_tls/`), README, CHANGELOG, `docs/THIRD_PARTY_LICENSES.md` (platform packages + image licence summary), `docs/architecture/contracts.md`, trackers, completion report |

---

## 6. Dependencies to add (platform)

| Package | Constraint | Licence | Reason |
|---|---|---|---|
| `docker` (Docker SDK for Python) | `>=7.1,<8` | Apache-2.0 | Engine API client (HC-14). Imported only by `sandbox/engine.py` and `sandbox/tls.py`'s adapter |
| — transitively `requests` | (via docker) | Apache-2.0 | Used by the SDK; import contract 7 still forbids our code from importing HTTP clients outside providers and the sandbox engine |
| — `pywin32` (Windows only, via docker) | (via docker) | PSF-2.0 | Installed by the SDK on Windows; not used by our TLS connection |

`urllib3` and `certifi` are already present (certifi accepted under KI-026). Before adding: `uv lock`, `pip-audit`, licence inventory test update (§12.3, tier 5). No new npm dependency for the platform UI. Packages inside the sandbox images are not platform dependencies; they are governed by §12.3 tiers 1–4.

---

## 7. Tests mapped to acceptance criteria

| Test | Marker | AC |
|---|---|---|
| Spec builder: every container has network none, limits, non-root, read-only root, `cap_drop ALL`, no host mounts, no socket; property test (hypothesis) that no recipe/argument combination loosens a limit | unit | AC-E-02, AC-E-04 |
| Recipe argument validation; unknown recipe refused; shell metacharacters impossible (argv only); no recipe calls `npm`, `npx` or `pip` | unit, security | AC-E-02 |
| Settings: plain `tcp://`, non-loopback hosts and Docker Desktop's pipe refused | unit, security | AC-E-02 |
| **Spike S-1 tests kept as permanent tests:** mutual TLS works; no-cert, wrong-CA and untrusted-server negative controls; wrong passphrase fails; no plaintext key on disk; verification flags enforced; static check for disabled verification | docker, security | AC-E-02 |
| **Spike S-2 tests kept as permanent tests:** offline React build, dependency paths, no download, image unchanged, restrictions effective (§3.5) | docker, security | AC-E-01, AC-E-02 |
| JUnit/coverage parser (valid, malformed, oversized; documents with DTD or entity declarations refused before parsing) | unit, security | — |
| Resource Guard: lease across two processes; LLM call waits while sandbox holds it and vice versa; stale lease takeover; low RAM → waiting for resources | integration | AC-E-02 |
| Docker absent (engine endpoint closed) → **Blocked**, no process started on the host | integration | AC-E-02, AC-E-04 |
| Static scan: no `subprocess`/`os.system`/`exec`/`spawn` in non-test platform code; negative control with a planted file | security | **AC-E-04** |
| **Isolation suite (`docker`, real engine):** outbound TCP and DNS fail; timeout kill; memory limit → OOM recorded; fork bomb stopped by the PID limit; write to root filesystem fails; host paths and home not visible; host environment canary not visible; container and volume removed after success, failure, timeout and cancel; leftover sweep; image digest mismatch refused | docker, security | **AC-E-02** |
| Each isolation test has a **negative control**: the same probe in a container built *without* that one control (test-only builder) succeeds, proving the test detects it | docker, security | AC-E-02 |
| Template self-tests offline for both templates → `TemplateVerification` `passed` | docker | **AC-E-01** |
| Template/image consistency: React `package.json` and Python requirements equal the image locks; manifest validation; protected-file checksum violation detected; `.env` rendering never writes secrets | unit, contract | — |
| Licence policy (§12.3): `images.lock.json` licence report satisfies the tier rules; a planted disallowed licence in an application dependency fails; an unknown licence fails | contract | — |
| Migration Runner: upgrade → downgrade → upgrade on disposable SQLite; a `postgresql` request refused with the D-16 reason | docker, unit | — |
| **E2E-3:** API queues verification → worker → integration service → sandbox → result visible via API, with peak memory, under the `low_memory` profile | docker, slow | **AC-E-01, AC-E-03** |
| Migration `0007` upgrade/downgrade; append-only trigger; rehearsal on a copy of the real DB | integration | — |
| Import contracts (3 new) with planted violations; schema snapshots; OpenAPI sync | contract | — |
| Regression: full suite of Parts A–D, including the `llm` tests (now taking the resource lease) | all | Regression |

Docker tests run locally only (CI keeps deselecting `docker`, like `llm`). Coverage targets: ≥ 90% for `devteam_harness.sandbox`, ≥ 80% for the services.

---

## 8. Security

- **No host execution path** (AC-E-04): static scan + no fallback branch in the runner + Docker-absent test.
- **Network:** `network_mode=none` on every container; no setting can change it. Images are built by a separate trusted script, the only step with network.
- **Supply chain:** base images pinned by digest; dependencies pinned with hashes; install scripts disabled (`npm ci --ignore-scripts`, `pip --no-deps --require-hashes`); audit results recorded; digest checked before every run.
- **Untrusted output:** stdout, stderr, JUnit and coverage are data: size-capped, safely parsed, labelled untrusted when shown to agents later (Part C input guard), never written to logs, audit or traces.
- **Secrets:** none enter a container. The environment comes only from the recipe; a canary secret must never appear in container environment, files or outputs (test).
- **Docker daemon:** reached only on `127.0.0.1` with mutual TLS (D-50, §3.11). Plain TCP, remote hosts and Docker Desktop's pipe are refused at config load. The client key is encrypted at rest; its passphrase lives only in Credential Manager; the decrypted key exists only in memory. Certificate and host-name verification cannot be switched off by configuration. The engine socket is never mounted into a container.
- **Approval:** execute tools require an approved, frozen spec (existing policy stage checks); verification requests require sign-in and are rate-limited and audited.
- **Cleanup:** `finally` removal + startup sweep by label.

---

## 9. Demonstration (G-E)

1. `scripts/doctor.py`: Docker Engine in WSL (`Ubuntu-24.04`) reachable over mutual TLS, not Docker Desktop, WSL cap set, images present and digests match.
2. Sandbox page → **Verify** both templates → both `passed`, offline, with duration and peak memory.
3. Blocked network: a probe container tries to reach the internet → refused; recorded.
4. Timeout: a sleeping recipe is killed at its limit; container gone (`docker ps -a` inside Ubuntu shows nothing with the label).
5. Docker stopped (`sudo systemctl stop docker` inside Ubuntu) → Verify → **Blocked: Docker engine not running**; nothing runs on the laptop.
6. LLM/sandbox lock: start a verification while a run's agent step is in progress → the verification waits for the model call to finish and the model to unload.
7. A connection attempt without the client certificate is refused by the engine.

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **S-1 fails:** the SDK cannot use an in-memory `SSLContext` with an encrypted key | Engine connection design blocked | Stop and report with evidence (§3.11); no weakened TLS or plaintext key |
| **S-2 fails:** Vite/TypeScript/Vitest cannot resolve packages from the image's `/node_modules` by the standard lookup without writing into the image | React template blocked | Stop and report; the HTML template and backend continue only with your approval |
| WSL localhost forwarding or idle shutdown behaves differently on this WSL version | Engine unreachable from Windows | Checked in S-1/S-3; `dev.ps1` keeps a session open; failure is **Blocked**, never bypassed. Free alternative to discuss if needed: WSL `networkingMode=mirrored` |
| Ubuntu's `docker.io` lacks a needed feature | Rework | D-51 fallback to Docker's free apt repository, with your approval |
| Docker Hub limits anonymous downloads (free, about 100 per 6 hours) | Image build delayed | Only 2 base images, pulled rarely; no account needed; wait and retry |
| Advisory databases unreachable during image build | Audit "not run" | Recorded honestly as not run; you acknowledge before use (D-08) |
| Client certificate expires (2 years) | Blocked | Doctor warns 30 days ahead; the setup script re-issues |
| Memory: WSL VM + Ollama + browser on ≈5–6 GB free (R2) | Runs wait or OOM | Ollama unloaded before every run; WSL cap 4 GB (D-45); container 1.5 GB; peak recorded (AC-E-03) |
| React build needs more than 1.5 GB or 600 s on this CPU | AC-E-01 for React | Measured in S-2/E.4; any increase only with evidence and your approval |
| Licence report finds an unknown or disallowed licence in a tool or application dependency | Image refused | Replace the package or bring the case to you (§12.3); never silently accepted |
| Long `docker` suite slows gates | Time | Kept out of the fast suite and CI; run at checkpoints and at the gate |
| Laptop sleep during long docker runs (KI-035) | Spurious failures | Same process-scoped keep-awake wrapper used in D |

---

## 11. Checkpoint breakdown and gate procedure

| Checkpoint | Exit criteria (internal) |
|---|---|
| E.1 | Engine set up (by you) and reachable; **S-1 passed** (all six requirements) or work stopped and reported; S-3 (archive copy, forwarding, idle) passed; plain TCP and Docker Desktop refused, with tests; dependency + licences + `pip-audit` clean; contracts + schemas; migration 0007 tested + rehearsal; settings; import contracts with planted violations; doctor checks; ADR-0008 with spike results; fast suite + static gates green; commit |
| E.2 | Spec builder, runner, guard, sweep; images built by the script, licence report and digests locked; **S-2 passed** (all five checks) or work stopped and reported; isolation tests for network, timeout, memory, PIDs, root FS, cleanup, Docker-absent, lock; commit |
| E.3 | 10 recipes, 3 tools with policy grants, JUnit/coverage parser, Migration Runner (SQLite only; PostgreSQL refusal); commit |
| E.4 | Both templates + manifests + `req` plugin; template/image consistency tests; Integration/Build base; self-tests pass offline; worker job, API, UI; OpenAPI/types; web suite; commit |
| E.5 | Full isolation suite with negative controls; E2E-3; peak memory under `low_memory`; full regression A–D (incl. `llm`); security, dependency, licence, secret scans; docs, runbook, trackers, completion report; commit; **stop for G-E** |

Each checkpoint runs its own tests and the static gates before the next begins. Gate procedure: roadmap §17.1. After G-E approval: merge locally, `v0.5.0`, `part-e-approved`; push only with your separate approval.

---

## 12. Free-only requirement (AP-019)

### 12.1 Definition

"Free-only" means: the normal development and testing workflow of the platform (set-up, running the app, running every test suite, building images, verifying templates, running sandboxes) **does not require**:

1. any paid subscription or licence (including Docker Desktop's paid tiers);
2. any paid API or AI service (models stay on the local Ollama);
3. any cloud-hosted infrastructure (build servers, hosted runners, hosted databases, cloud storage);
4. any paid container registry or package registry;
5. any proprietary paid dependency;
6. creating an account for a paid service (no account of any kind is needed for Part E);
7. any paid deployment or hosting.

It also excludes **recurring or usage-based costs** of any kind. Free public sources that are downloaded from (Ubuntu archive, PyPI, npm registry, Docker Hub anonymous pulls, public advisory databases) are allowed, because they cost nothing and need no account. After set-up, all sandbox work runs **offline**.

Explicitly **not used**: Docker Desktop, Docker Hub accounts or paid registries, Docker Scout, Testcontainers Cloud, cloud build or CI minutes, paid AI or APIs. The `.github/workflows/ci.yml` inside each template is a **file delivered** with the generated app; the platform never runs it.

### 12.2 Components by category

**(a) Existing host components (already installed, nothing new to pay for):**

| Component | Licence | Role in E |
|---|---|---|
| Windows 11 | (already owned) | Host |
| WSL 2.7.11 | MIT (open source since 2025) | Linux VM |
| Ubuntu 24.04 in WSL | Free distribution (GPL, LGPL, MIT, BSD and other free licences) | Runs Docker Engine |
| OpenSSL (in Ubuntu) | Apache-2.0 | Certificates at set-up |
| Python 3.12, uv, Node 22, Git, Gitleaks, Ollama | PSF-2.0, MIT/Apache-2.0, MIT, GPL-2.0 (tool only), MIT, MIT | Existing tools, unchanged |
| Docker Desktop 29.6.2 | Proprietary (free tier only for some users) | **Installed but not used**; refused by the platform |

**(b) New open-source components:**

| Component | Licence | Installed from |
|---|---|---|
| `docker.io`, `containerd`, `runc`, `docker-buildx` | Apache-2.0 | Ubuntu archive (`apt`) |
| Docker SDK for Python and its dependencies (`requests`, `pywin32`) | Apache-2.0, Apache-2.0, PSF-2.0 | PyPI (`uv sync`) |
| Base images `python:3.12-slim`, `node:22-slim` | Debian packages under their own free licences; CPython PSF-2.0; Node.js MIT; npm Artistic-2.0 | Docker Hub, anonymous |
| Image packages (§3.5) | MIT, BSD-3-Clause, Apache-2.0 and others, checked by §12.3 | PyPI and npm registry |

**(c) External downloads, their source and offline status:**

| Download | Source | When | Needed again? | Offline afterwards? |
|---|---|---|---|---|
| Docker Engine packages | `archive.ubuntu.com` (and Ubuntu security mirror) | Set-up; later normal Ubuntu updates | Only for updates | Yes |
| Docker SDK for Python | PyPI | `uv sync` after the dependency is added | No (cached in the uv environment) | Yes |
| Base images | Docker Hub (`registry-1.docker.io`), anonymous | Image build | Only when a pinned digest changes | Yes |
| Image packages | PyPI, npm registry | Image build | Only when a lock changes | Yes |
| Vulnerability advisories | OSV / PyPI advisory API (`pip-audit`), npm registry audit endpoint (`npm audit`) | Image build | Each image rebuild | Yes; if unreachable, recorded as "not run" |

Template verification, sandbox runs, all tests and normal platform use need **no network** once these are done (Ollama is already local).

### 12.3 Licence policy (replaces revision 2's blanket GPL ban)

Images are built from Debian-based official images, which **do** contain GPL- and LGPL-licensed operating-system components (for example `bash`, `coreutils`, `glibc`). The plan never claims an image is GPL-free. Instead, licences are handled in tiers:

| Tier | What | Rule | Verified by |
|---|---|---|---|
| **1. Operating-system / base-image layer** | Debian packages in the pinned base image (as shipped by the distribution) | **Allowed under any free licence, including GPL and LGPL.** Packages are not modified. The licence of every package is recorded (package name, version, `/usr/share/doc/<pkg>/copyright`) | `licence_report` tool runs `dpkg-query` and collects copyright files inside the built image; result stored in `images.lock.json` |
| **2. Language runtimes** | CPython in the Python image; Node.js and npm in the Node image (as shipped by the base image) | Allowed (PSF-2.0, MIT, Artistic-2.0) | Recorded in the report |
| **3. Dependencies installed for project recipes** | Packages preinstalled only to build, test or check code (pytest, pytest-cov, ruff, bandit, typescript, vite, vitest, jsdom, their dependencies) | **Allowed:** permissive licences (MIT, BSD-2/3-Clause, Apache-2.0, ISC, PSF-2.0, 0BSD, Unlicense, CC0-1.0, BlueOak-1.0.0, Python-2.0, Zlib), and MPL-2.0 and LGPL **only as unmodified separate packages**, each listed for your review in the report. **Refused:** GPL-2.0/3.0 and AGPL as tool dependencies; proprietary, "no licence" or unknown | `licence_report` reads Python package metadata (`License-Expression`, `License`, classifiers) and every `package.json` `license` field under `/node_modules`; compares with `licence_policy.yaml` |
| **4. Application runtime dependencies** | Packages a generated application itself needs at run time and that are delivered to the user (FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, React, React DOM) | **Permissive only** (same list as tier 3, without MPL-2.0 and LGPL), because these travel with every generated application and may later be published (Part P). Exceptions only with your explicit approval, recorded in `licence_policy.yaml` | Same tool; tier-4 packages are marked in the template manifest |
| **5. Platform dependencies** | Python packages of the DevTeam AI platform itself (e.g. the Docker SDK) | Existing Part D rule: permissive; MPL-2.0 accepted case by case (orjson KI-023, certifi KI-026); no GPL-family | Existing `tests/contract/test_third_party_licences.py`, extended |
| **6. Code bundled into generated applications** | Our own template files and plugins (`devteam_req`, app skeleton) | Our own code only; no third-party source copied in; licence decided with D-21 (D-52) | Review; contract test that template folders contain no vendored third-party code (no `node_modules`, no `site-packages`, no copied licence headers) |

**Obligations (documented in `docs/THIRD_PARTY_LICENSES.md`, new section "Sandbox images"):**

- Images are built and used **only on this laptop** and are never pushed to a registry or distributed. If that ever changes, GPL/LGPL source-availability obligations for tier 1 apply and need a separate decision.
- Licence and copyright notices of all packages are preserved, as installed by the distribution or package manager.
- No package of any tier is modified.
- Unknown or missing licence → the image build fails; it is not accepted by default.

**Automation:** `scripts/build_sandbox_images.py` runs the licence report for every image build and refuses to record the image digest if any rule fails. A contract test re-checks the committed `images.lock.json` against `licence_policy.yaml` on every test run (no Docker needed for that check).

---

## Owner confirmation

☑ D-07 (b) Docker Engine inside WSL2 (AP-019)

Awaiting approval: ☐ D-04 · ☐ D-08 · ☐ D-16 (deferral to K) · ☐ D-24 · ☐ D-45 · ☐ D-46 · ☐ D-47 · ☐ D-48 · ☐ D-49 · ☐ D-50 (subject to S-1) · ☐ D-51 · ☐ D-52 · ☐ licence policy §12.3 · ☐ container limit values (§3.2) · ☐ start implementation (after G-D)
