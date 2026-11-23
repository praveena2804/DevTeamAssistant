# Part E — Isolated Execution Sandbox & Target-App Templates — Plan

Roadmap ref: §13 Part E, §3.2, §5.1 (HC-14, HC-06, HC-05), §6.6, §19 (R2, R4, R5) · Milestone: M1 · Gate: **G-E** · Working Plan §9 (TS-04…TS-08, TS-11), §20 Part E · Spec §7.2, §9.3, §11.2–11.3, §13.1, §14.1, §18, §22, §25 · Date: 2026-11-23

| Field | Value |
|---|---|
| Status | **Final draft for the owner's approval** (revision 2). Not confirmed; no Part E code written |
| Owner rulings so far | **D-07 = (b) Docker Engine inside WSL2** (AP-019). **Completely free implementation:** free and open-source tools only; no paid subscriptions, proprietary paid dependencies or cloud-service costs (AP-019, §12) |
| Preconditions | **G-D approved: not yet** (Part D awaits your review). D-04, D-08, D-16, D-24 and the new D-45…D-51 confirmed: not yet (§2) |
| Harness components | Implements **HC-14** (sandbox). Extends **HC-06** (execute tools), **HC-05** (Resource Guard, LLM/sandbox lock), HC-21 (audit of every execution), HC-11 (spans) |
| Agents | **None.** No agent behaviour changes. The ten agents stay exactly as in Part D |
| Branch | `part/e-sandbox`, created from `main` **after** G-D is approved and Part D is merged. One local commit per checkpoint E.1–E.5. No push without your separate approval |

---

## 0. Part E in simple words

Parts C and D built the safety walls and the assembly line. The agents can write files, but **nothing the platform generates has ever been run**. Part E builds the **locked room** where generated code is allowed to run, and the two **starter kits** every generated application begins from.

When it is done:

1. **Generated code runs only inside a throw-away Docker container.** The container:
   - has **no network at all**;
   - has strict limits on memory, CPU, number of processes and time;
   - runs as a normal (non-root) user, with a read-only system disk;
   - cannot see your home folder or any platform folder;
   - is deleted after every run; only the logs and results are kept.
2. **There is no "run it on the laptop instead" fallback.** If Docker is missing or stopped, the work is marked **Blocked** with the reason. It never runs unsafely.
3. **The AI and the sandbox never run at the same time.** Before a container starts, the platform asks Ollama to unload its model and checks free memory. If memory is short, the run waits.
4. **Agents cannot type commands.** They can only ask for named, pre-approved actions ("recipes"), such as "run the API tests" or "build the frontend". Each recipe has a fixed command and checked arguments.
5. **Two starter kits** (templates), each with a manifest that says which files agents may fill in and which are protected:
   - `react_fastapi_sqlite` — React + Vite + TypeScript frontend, FastAPI backend, SQLite database;
   - `html_fastapi_sqlite` — plain HTML/CSS/JS frontend, same backend.
6. **Each starter kit proves itself.** Its own tests and build run inside the sandbox, offline, and the result is stored as a *template verification* record. Part G will only call a technology "supported" when that record says it passed.
7. **Everything is free and open source.** Docker Engine runs inside the Ubuntu system already installed in WSL2 on this laptop. Docker Desktop is **not** used. Every tool, library and container image is listed with its licence and cost in §12. Nothing needs an account, a subscription or a cloud service.
8. **What Part E does not do:** no agent writes real application code yet (K, L); real test results for generated projects arrive in M; no repair loop (N); no packaging (O); nothing is published (P).

---

## 1. Scope

**In scope:** roadmap §13 Part E and Working Plan §20 Part E, delivered as five internal checkpoints under one gate (the D-32 pattern used in C and D).

| Checkpoint | Working Plan steps | What |
|---|---|---|
| **E.1 Foundations** | TS-04 (contracts), TS-07 (detection) | Decisions recorded. **Docker Engine in WSL2 set up by you with the runbook and setup script** (§3.10): free Ubuntu packages, mutual-TLS loopback connection, WSL memory cap. Docker SDK dependency with licence check. Contracts: `ContainerLimits`, `RecipeSpec`, `SandboxExecution`, `TemplateManifest`, `TemplateVerification`. Migration `0007_sandbox`. Settings section `sandbox`. Import rules. Doctor: Docker engine, WSL memory cap, images. **Spike:** confirm on this laptop (1) WSL localhost forwarding and idle behaviour, (2) the SDK's mutual-TLS connection using an encrypted client key without writing it decrypted to disk, (3) the copy-in/copy-out mechanism (§3.3) |
| **E.2 Sandbox runner & Resource Guard** | TS-04, TS-07, TS-08 | `ContainerSpec` builder (limits can never be loosened). Runner: create → copy in → start → watchdog → collect → remove (always, in `finally`). Peak-memory sampling. Startup sweep of leftover containers. Resource Guard: cross-process LLM/sandbox lock, Ollama unload, free-RAM check, Docker availability → **Blocked**. Offline image Dockerfiles + trusted build script + digest lock |
| **E.3 Recipes, tools, runners** | TS-05, TS-06 | Recipe registry (10 recipes, argument schemas). Tools `request_build`, `request_test_run`, `request_migration_check` (side effect `execute`). JUnit XML and coverage parsing (test runner base). Migration Runner (disposable SQLite; PostgreSQL behind the D-16 flag) |
| **E.4 Templates & Integration/Build Service base** | Templates, self-test | Both templates with `template.yaml` manifests and the `req` pytest marker plugin. Integration/Build Service base: copy a template into `generated/`, protect files by checksum, render `.env` from placeholders. Template self-test → `TemplateVerification` records. Worker job, API endpoints and a small UI panel to request and view verifications |
| **E.5 Isolation suite, E2E-3, evidence** | TS-11, E2E-3 | `docker` isolation suite with negative controls. E2E-3 (template → integration service → sandbox → results via API). Peak memory under `low_memory` (AC-E-03). Docs, runbook, trackers, completion report |

**Out of scope:**

| Excluded | Comes in |
|---|---|
| Agents generating real application code; any change to agent behaviour | K, L |
| Wiring the run graph's `integration_build` and `test_runner` nodes to the sandbox. They stay honest stand-ins in E (§2, D-47) | K/L (build), M (tests) |
| `test_runs` table, requirement evidence, trivial-test detector | M |
| `apply_patch` tool, repair loop, Completion Gate | N |
| Building the generated app's own `Dockerfile` / `docker-compose` (needs network; they are delivered as files only) | O (packaging notes) |
| Browser (Playwright) tests | L/N (D-09) |
| Capability Catalog and feasibility (they **consume** `TemplateVerification`) | G |
| `local_password_rbac` template variant (if D-04 (b) is chosen) | K |
| Any network access from a sandbox, or an "approved endpoints" allowlist (Spec §11.3) | Not in MVP unless separately approved |

---

## 2. Decisions needed

### 2.1 Roadmap decisions due in Part E

| ID | Decision | Options | Recommendation and reason |
|---|---|---|---|
| **D-07** | Docker distribution (ADR-0007) | (a) Docker Desktop; (b) Docker Engine inside WSL2; (c) Podman | **Ruled (AP-019): (b) Docker Engine inside WSL2**, in the existing `Ubuntu-24.04` distribution (systemd already enabled). Docker Desktop stays installed but is **not used**: the platform refuses its connection and refuses an engine that reports itself as Docker Desktop. ADR-0007 becomes *Accepted (b)* |
| **D-04** | Authentication pattern for generated apps | (a) `no_auth` only; (b) also `local_password_rbac` | **(a) for Part E.** Both templates ship without login. The manifest reserves an `auth` slot so (b) can be added in Part K as a template extension with its own verification. Choosing (b) now adds template scope, security tests and a password-hashing dependency to E |
| **D-08** | Offline sandbox vs dependency installs and audits | (a) Pre-built images with allowlisted dependencies; audits run in a separate allowlisted step; (b) disclose that audits are unavailable | **(a) with (b) as fallback.** Dependencies are installed **only when the trusted image-build script runs** (network at build time only). The same script runs `pip-audit` and `npm audit` against the image's locked dependencies and stores the result next to the image digest. Inside the sandbox there is never network. A template that needs a package not in its image's allowlist is refused, not fetched |
| **D-16** | PostgreSQL in Docker | Include behind a flag / defer | **Behind a flag, off by default** (`sandbox.postgres.enabled: false`). E implements the Migration Runner's PostgreSQL mode (disposable container, random credentials in environment only, container and volume destroyed after) and its `docker` test. It is enabled only after a measured run under `low_memory` shows enough free memory. With the flag off, a PostgreSQL request is reported "not enabled", never silently replaced by SQLite |
| **D-24** | Integration/Build Service classification | — | **Confirm: deterministic service, not an agent** (principal `service:integration_builder`, already declared in Part C IAM) |

### 2.2 New decisions for Part E (proposed rulings)

| ID | Question | Options | Recommendation and reason |
|---|---|---|---|
| **D-45** | Docker/WSL memory cap (Spec §25 lists it as configuration) | (a) Runbook tells you to set `%USERPROFILE%\.wslconfig` (`memory=4GB`, `processors=2`, `swap=2GB`, `autoMemoryReclaim=gradual`); doctor warns if it is missing or larger; the platform never edits files in your home folder. (b) Platform writes `.wslconfig` itself | **(a).** No `.wslconfig` exists today; the WSL VM currently sees 7.8 GB (checked 2026-11-23). With Docker Engine inside WSL, this cap is the ceiling for all containers together. 4 GB leaves room for Ollama on Windows (≈2.1 GB for a 3B model, unloaded before any sandbox run anyway), the API, the worker and a browser. `autoMemoryReclaim=gradual` (a free WSL setting) returns memory to Windows after builds. Writing to your home folder is outside the platform's scope |
| **D-46** | The LLM/sandbox lock must work across processes. Today `MODEL_LOCK` is a thread lock inside one process | (a) One SQLite-backed resource lease (`resource_lock` row, like the worker lease) taken by both the gateway and the sandbox, with heartbeat and stale takeover. (b) An OS file lock in `data/` | **(a).** Same proven pattern as the D.4 worker lease, visible in the database, recoverable after a crash. The existing in-process `MODEL_LOCK` stays as an extra inner guard |
| **D-47** | Should E connect the sandbox to the run graph? | (a) No. The `integration_build` and `test_runner` nodes stay honest stand-ins; the sandbox is reached through template verification jobs and the new tools (which no agent uses until K). (b) Make `integration_build` copy the template and build it during every run | **(a).** In Part D no agent writes code, so building in a run would only rebuild an empty starter kit and could be mistaken for building the project. Wiring happens in K/L, when there is real code to build |
| **D-48** | How files get into and out of a container | (a) **No host folder mounted.** Files are copied into the container through the Docker API archive call and results copied back out. (b) Bind-mount one disposable staging copy of `generated/` (roadmap TS-04 wording) | **(a), confirmed by the E.1 spike; (b) as fallback.** With (a) no host path is ever visible to the container, so mount-escape problems cannot occur, and Windows folders never have to be shared into the WSL engine (a Windows-to-WSL bind mount would also be slow and translate permissions badly). If the spike shows (a) cannot work with a read-only root plus a writable work volume, (b) is used with a per-execution copy (never `generated/` itself) and the escape tests still apply |
| **D-50** | How the platform (Windows) reaches Docker Engine (inside WSL2). The engine's own socket lives inside the Linux VM; Windows programs cannot open it directly | (a) **Mutual-TLS on loopback:** the engine listens on `127.0.0.1:2376` inside WSL (forwarded to Windows `127.0.0.1` by WSL's built-in localhost forwarding) and accepts only clients presenting a certificate from a private certificate authority made for this laptop. (b) Plain TCP on loopback, no authentication. (c) SSH transport. (d) Move the worker into WSL | **(a).** Free (OpenSSL plus Docker's own TLS support), documented by Docker, and needs no extra program. **(b) is refused:** any local program, and even a web page through the browser, could then drive the engine with root power inside WSL. (c) needs `paramiko` (LGPL) or running `ssh.exe` from the harness. (d) changes the approved architecture (D-41). Details: the client private key is stored **encrypted** (AES-256) in `data/sandbox_tls/`; its passphrase lives only in Windows Credential Manager (D-18); the CA's private key is deleted after the certificates are issued; the server key stays inside WSL (root-only). The setting `sandbox.docker_host` accepts only `tcp://127.0.0.1:<port>` **with** TLS verification; plain `tcp://`, remote hosts and Docker Desktop's pipe are rejected at startup |
| **D-51** | Where Docker Engine comes from | (a) Ubuntu's own archive packages `docker.io` and `docker-buildx` (Moby, Apache-2.0). (b) Docker's apt repository (`docker-ce`, also free, Apache-2.0) | **(a).** Free, open source, security updates through normal Ubuntu updates, no third-party package repository or signing key to add. The engine version is recorded by the doctor and in every `SandboxExecution`. If a needed feature is missing from Ubuntu's version, (b) is the free fallback, with your approval |
| **D-49** | Who may request a template verification | (a) You, from the UI or `scripts/verify_templates.py`; it is queued and run by the worker (D-41: only the worker executes). (b) Automatically at every start | **(a)**, plus an automatic "stale" flag when a template's or image's checksum changes. A verification run takes minutes and needs Ollama unloaded, so it should not start on its own |

---

## 3. Architecture

### 3.1 Package layout and dependency rules

| Code | Package | Notes |
|---|---|---|
| Sandbox runner, `ContainerSpec` builder, recipe registry, Resource Guard, execute tools | `devteam_harness.sandbox` (new subpackage): `spec.py`, `engine.py` (the **only** module that imports `docker`), `runner.py`, `recipes.py`, `guard.py`, `results.py` (JUnit/coverage parsing), `tools.py` | Harness = HC-14 |
| Ollama "unload model" and "what is loaded" calls | `devteam_harness.llm_gateway.providers.ollama` (`keep_alive: 0`, `/api/ps`) | HTTP stays in providers (import contract 7) |
| Integration/Build Service base, Migration Runner, Test Runner base, template self-test | `devteam_services.{integration_builder, migration_runner, test_runner, templates}` | Deterministic services; call the sandbox only through the harness |
| Template verification job | `devteam_worker` (new job kind) | The worker stays the only executor |
| Verification request and read endpoints | `devteam_api` (`routers/sandbox.py`) | API writes a request row; never touches Docker |
| Templates | `templates/target_apps/{react_fastapi_sqlite,html_fastapi_sqlite}/` | Data, not platform code: excluded from platform ruff, mypy, pytest collection and the npm workspace |
| Images | `sandbox/images/{python_api,node_web}/Dockerfile`, `sandbox/images/images.lock.json` | Built only by `scripts/build_sandbox_images.py` |

**New import-linter contracts** (each with a planted-violation test, as in C and D):

1. Only `devteam_harness.sandbox.engine` may import `docker`.
2. Agents, orchestration, the API and the worker may not import `devteam_harness.sandbox.engine` (they go through tools, services or the job interface).
3. The API may not import `devteam_services.{integration_builder,migration_runner,test_runner,templates}` execution functions (read-only queries only).

**AC-E-04 static rule (no host execution):** a test scans every non-test module in `packages/`, `apps/` and `agents/` and fails on `subprocess`, `os.system`, `os.exec*`, `os.spawn*`, `os.startfile`, `asyncio.create_subprocess_*` and `pty`. Allowlist: none. Today only tests and `scripts/` use `subprocess` (checked 2026-11-23). `agents/ruff.toml` also gets the missing `docker` ban that `CLAUDE.md` already lists.

### 3.2 Container limits (values proposed for confirmation)

Every container is built by one function. The limits are **minimums of security, not settings an agent or recipe can relax**: a recipe may ask for *less* memory or time, never more than the profile ceiling.

| Control | `low_memory` (default) | `standard` | Fixed (not configurable) |
|---|---|---|---|
| Network | — | — | `network_mode="none"` |
| Memory (hard, no extra swap) | 1,536 MB | 2,048 MB | `memswap_limit = mem_limit` |
| CPUs | 1.5 | 2.0 | — |
| Processes | 256 | 256 | `pids_limit` always set |
| Wall clock (ceiling) | 600 s | 900 s | Watchdog kills, then removes |
| `/tmp` tmpfs | 256 MB, `noexec,nosuid` | 256 MB | — |
| Work area `/work` | anonymous volume, 512 MB copy-in cap | same | Removed with the container |
| Output kept per stream | 1 MB (head and tail kept, middle cut and marked) | same | — |
| User | — | — | `10001:10001`, never root |
| Root filesystem | — | — | `read_only=True` |
| Privileges | — | — | `cap_drop=["ALL"]`, `no-new-privileges`, not privileged, no devices, `ipc_mode="private"`, no host PID/IPC/UTS |
| Mounts | — | — | **None from the host** (D-48). Never the Docker socket |
| Environment | — | — | Only what the recipe declares (e.g. `DATABASE_URL=sqlite:////work/.devteam/test.db`, `CI=1`). Host environment is never passed through |
| Labels | — | — | `devteam.sandbox=1`, `devteam.execution_id`, `devteam.project_id` (for the startup sweep) |

Settings: new `sandbox` section in `config/defaults.yaml` and profiles (validated, unknown keys rejected): `enabled`, `docker_host` (default `tcp://127.0.0.1:2376`; only loopback with mutual TLS is accepted, D-50), `tls_dir` (default `data/sandbox_tls`), `wsl_distribution` (default `Ubuntu-24.04`, used by the doctor and `dev.ps1` only), the limits above, `min_free_ram_mb` (2,048 on `low_memory`), `unload_models_before_run: true`, `postgres.enabled: false`, `image_lock_path`.

### 3.3 Execution flow (one sandbox run)

1. **Request.** A tool call (`request_test_run`, …) or a service asks for recipe *R* with arguments *A* on project *P*. Arguments are validated against *R*'s schema; unknown recipes and extra arguments are refused.
2. **Policy.** The Tool Layer checks role grant, stage and approved spec (Part C engine). Execute tools are allowed only in stages `build`, `delivery` and `change_request`, which already require an approved, frozen spec.
3. **Resource Guard.**
   1. Docker check: the SDK reaches the engine over mutual TLS (`ping`); the engine is Linux and is **not** Docker Desktop; the image exists **and its digest matches `images.lock.json`**. If not → **Blocked** with a specific reason (`sandbox.docker_unavailable`, `sandbox.tls_not_configured`, `sandbox.engine_not_allowed`, `sandbox.image_missing`, `sandbox.image_digest_mismatch`). Never a host fallback.
   2. Take the cross-process resource lease (D-46). If an LLM call holds it → wait.
   3. Ask Ollama to unload loaded models and confirm with `/api/ps`. If Ollama is not running, nothing is loaded: continue.
   4. Free RAM ≥ `sandbox.min_free_ram_mb` → continue; otherwise release the lease and return **waiting for resources** (the existing Part D mechanism).
4. **Run.**
   1. Create the container (§3.2) without starting it.
   2. Copy in a tar of the allowed files only: the workspace's `generated/` tree, filtered (no symlinks, no files outside, size cap, no `.env` other than the rendered placeholder one).
   3. Start. Sample memory from the stats stream every second (peak recorded).
   4. Watchdog: kill at the wall-clock ceiling.
5. **Collect.** Exit code, OOM flag, capped stdout/stderr, and declared result files (`/work/.devteam/out/junit.xml`, `coverage.xml`) copied out → `workspaces/<P>/runs/<run_or_verification_id>/sandbox/<execution_id>/`.
6. **Remove.** Container and its volume removed in `finally`, including after errors and cancellation. The worker's startup sweep removes any `devteam.sandbox=1` container left by a crash.
7. **Record.** One `sandbox_executions` row, audit event `sandbox.executed`, a trace span (no file content or output text in audit, traces or logs). Lease released.

### 3.4 Recipes (TS-05)

All commands are fixed argument lists (never a shell string). Arguments are only enum values or validated relative paths inside `/work`.

| Recipe | Image | Command (fixed) | Arguments allowed | Outputs | Tool |
|---|---|---|---|---|---|
| `pytest_api` | `python_api` | `python -m pytest -p devteam_req -m "not db" --junitxml=… --cov=app --cov-report=xml:… tests` | `select`: marker enum (`api`, `smoke`, all) | JUnit, coverage | `request_test_run` |
| `pytest_db` | `python_api` | same with `-m db` | — | JUnit, coverage | `request_test_run` |
| `alembic_upgrade` | `python_api` | `alembic upgrade head`, then `downgrade base`, then `upgrade head` on a disposable SQLite file | — | log, final revision | `request_migration_check` |
| `alembic_autogenerate` | `python_api` | `alembic revision --autogenerate -m <slug>` on a disposable DB | `message`: slug `[a-z0-9_]{1,40}` | new revision file (returned as a proposed diff; nothing written to `generated/` by the sandbox) | `request_migration_check` |
| `ruff_check` | `python_api` | `ruff check app tests --output-format json` | — | findings | `request_build` |
| `bandit_scan` | `python_api` | `bandit -r app -f json` | — | findings | `request_build` |
| `npm_build` | `node_web` | `npm run build --offline` (dependencies preinstalled in the image, linked read-only) | — | log, `dist/` file list | `request_build` |
| `tsc_check` | `node_web` | `npx --offline tsc --noEmit` | — | diagnostics | `request_build` |
| `vitest_run` | `node_web` | `npx --offline vitest run --reporter=junit` | — | JUnit | `request_test_run` |
| `static_validation` | `python_api` | `python -m devteam_static_check frontend` (bundled in the image: HTML parse, broken local links/scripts, no external URLs) | — | findings | `request_build` |

Tool grants follow `docs/architecture/HARNESS_ASSESSMENT.md` (keys B/T/M): Database Developer M; Backend B, T; Frontend B; Tester T; Debugger B, T. Added to `config/guardrails/policies.yaml`, `KNOWN_TOOLS` and `STAGE_CEILINGS`. The Part C guard "execute tools arrive with the sandbox (E)" is lifted **for `execute` only**; `external` stays refused until Part P. **No agent calls these tools in Part E** (agent behaviour is unchanged); they are tested directly through the Tool Layer with real Docker.

Test results: the test runner base turns JUnit into counts and per-test outcomes, stored on the `sandbox_executions` row and (for self-tests) in `TemplateVerification`. The `TestRun` records and their table, which only the Test Runner may write, arrive in Part M with requirement evidence.

### 3.5 Images (TS-08, D-08)

| Image | Base (pinned by digest) | Preinstalled (allowlist = the template's manifest) |
|---|---|---|
| `devteam-sandbox/python_api` | `python:3.12-slim` | fastapi, uvicorn, pydantic, sqlalchemy, alembic, httpx (test client), pytest, pytest-cov, ruff, bandit, the `devteam_req` plugin, the static checker. Exact versions pinned with hashes in `sandbox/images/python_api/requirements.lock` |
| `devteam-sandbox/node_web` | `node:22-slim` | react, react-dom, vite, @vitejs/plugin-react, typescript, vitest, jsdom. Pinned by `sandbox/images/node_web/package-lock.json` |

- Built only by `scripts/build_sandbox_images.py` (run by you once, and whenever a lock changes). It is the **only** step with network access. It records each image's digest, licences of installed packages and the `pip-audit`/`npm audit` result in `sandbox/images/images.lock.json` (committed).
- At run time an image whose digest does not match the lock is refused.
- No package with an AGPL/GPL-family licence enters an image (checked by the build script and a contract test).
- Estimated download: ≈150 MB (python slim) + ≈80 MB (node slim) + dependencies; disk ≈1.5 GB in total.

### 3.6 Templates

Location: `templates/target_apps/<template_id>/`. Both share the backend; they differ in the frontend.

| Part | Contents |
|---|---|
| Backend `backend/` | FastAPI app factory; `DATABASE_URL` from environment (SQLite default `sqlite:///./data/app.db`); SQLAlchemy 2 models base; Alembic with one baseline revision; `/health` and `/ready`; standard error format (`{"error": {"code", "message", "request_id"}}`); JSON logging; `tests/` with API and DB smoke tests using the `req` marker |
| `req` marker plugin | `@pytest.mark.req("FR-001", "BR-002")`; writes requirement IDs into JUnit properties so Part M can map tests to requirements. Its own tests are part of the self-test |
| Frontend (`react_fastapi_sqlite`) | Vite + React + TS strict; a typed API client folder (generated later from OpenAPI in K/L); one health-status page; Vitest smoke test |
| Frontend (`html_fastapi_sqlite`) | `index.html`, `styles.css`, `app.js` calling `/health`; checked by `static_validation` |
| Delivery files | `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.env.example` (placeholders only), `README.md.tmpl`, `.gitignore`. Delivered, **not** built or executed by the platform in E (they need network) |
| `template.yaml` | `id`, `version`, `checksum` (computed), `writable_slots` (globs agents may fill, e.g. `backend/app/models/**`, `backend/app/api/**`, `frontend/src/pages/**`, `backend/tests/**` for the Tester only), `protected_files` (e.g. `backend/app/main.py`, `alembic/env.py`, `.github/**`, the plugin), `recipes` (build/test/migration recipe IDs), `image` per recipe, `dependency_allowlist` (must be a subset of the image's), `env_placeholders`, `auth: none` (D-04) |

**Integration/Build Service base:** materialise a template into `workspaces/<P>/generated/` only if it is empty; record checksums of protected files (any later change is reported as a violation in K/L); render `.env` from `env_placeholders` (non-secret values only, e.g. the SQLite path; anything secret-like is left as a placeholder). It does **not** build in a run in E (D-47).

**Template self-test:** materialise the template into a temporary verification workspace, then run its recipes in the sandbox (python: `ruff_check`, `alembic_upgrade`, `pytest_api`, `pytest_db`; react: also `tsc_check`, `vitest_run`, `npm_build`; html: `static_validation`). Result: one `TemplateVerification` record with status `passed` / `failed` / `blocked`, per-recipe outcome, template checksum, image digests, platform version, duration and peak memory. A changed template or image makes earlier records `stale`.

### 3.7 Contracts added (C.1 rules: version + JSON schema snapshot)

| Contract | Key fields |
|---|---|
| `ContainerLimits` v1 | memory, cpus, pids, wall clock, tmpfs size, output cap |
| `RecipeSpec` v1 | id, image, argv template, argument model name, timeout, outputs, tool |
| `SandboxExecution` v1 | `sbx_` ID, project, run or verification ID, recipe, image digest, limits applied, status (`completed` / `failed` / `timed_out` / `oom_killed` / `blocked` / `cancelled`), exit code, duration, peak memory, test counts, output refs, reason |
| `TemplateManifest` v1 | as `template.yaml` above |
| `TemplateVerification` v1 | `tvf_` ID, template ID, version, checksum, status (`queued` / `running` / `passed` / `failed` / `blocked` / `stale`), per-recipe results, image digests, duration, peak memory, requested by, timestamps |

New ID prefixes `sbx` and `tvf`; events `sandbox.executed`, `template.verification_recorded`; audit actions `sandbox.executed`, `sandbox.blocked`, `template.verified`. Expected schema count: 92 → about 99.

### 3.8 Data: migration `0007_sandbox`

| Table | Purpose |
|---|---|
| `sandbox_executions` | One row per container run (the `SandboxExecution` contract). Append-only by trigger, like `agent_outputs` |
| `template_verifications` | Requests and results. Status changes only forward (`queued → running → passed/failed/blocked`, any → `stale`) |
| `resource_lock` | Single row: holder (`llm` / `sandbox`), owner ID, heartbeat (D-46) |

Every domain row carries `project_id` (verifications use a reserved platform project ID, as the audit log does for platform events). Upgrade and downgrade tested; the existing database is backed up automatically before migration (Part A behaviour); rehearsal on a copy of `data/state.db` as in Part D.

### 3.9 API and UI

| Endpoint | Purpose |
|---|---|
| `GET /api/sandbox/status` | Docker reachable, engine version, images present and digests match, WSL cap check, lock holder |
| `GET /api/sandbox/templates` | Templates with their latest verification |
| `POST /api/sandbox/templates/{template_id}/verifications` | Queue a verification (rate-limited, audited); the worker runs it |
| `GET /api/sandbox/verifications/{id}` | Result, per-recipe outcome, peak memory, log references |

UI: a small **Sandbox** page (linked from the header): Docker status, the two templates, "Verify" button, results. OpenAPI and web types regenerated. Under D-15, platform UI is checked by API E2E plus a manual demo.

### 3.10 Docker Engine inside WSL2 (D-07 (b), D-50, D-51)

Current state (checked read-only, 2026-11-23): WSL 2.7.11; `Ubuntu-24.04` with **systemd enabled**; OpenSSL present; Docker Engine **not installed**; no `.wslconfig`.

**One-time set-up, done by you** (it needs your Ubuntu `sudo` password, which the platform never sees or stores). `docs/runbooks/docker-setup.md` gives every command; `scripts/setup_wsl_docker.ps1` runs the same steps and stops at the first error.

1. Optional: set `.wslconfig` (D-45), then `wsl --shutdown`.
2. Inside Ubuntu: `sudo apt install docker.io docker-buildx` (D-51); `sudo systemctl enable --now docker`.
3. Certificates (OpenSSL inside Ubuntu):
   - a private CA, valid 2 years;
   - a server certificate for `127.0.0.1` and `localhost`; its key stays in `/etc/docker/tls/` (root only);
   - a client certificate whose key is **encrypted** with a random passphrase. The passphrase is passed through standard input (never on a command line), then saved in Windows Credential Manager under the `devteam-ai` entry;
   - the CA key is deleted.
4. The engine listens on its normal Unix socket **and** on `tcp://127.0.0.1:2376` with `tlsverify` (a systemd override plus `/etc/docker/daemon.json`).
5. Client certificate, encrypted key and CA certificate are copied to `data/sandbox_tls/` (git-ignored, like all of `data/`).
6. `uv run python scripts/doctor.py` confirms everything; `uv run python scripts/build_sandbox_images.py` builds the two images (the only step with network).

**Day-to-day:** `scripts/dev.ps1` starts the Ubuntu distribution and keeps one idle session open while the platform runs, because WSL stops an idle distribution. If the engine is not reachable, sandbox work is **Blocked** with the reason, as for "Docker absent".

**Platform code** never runs `wsl.exe`, `docker.exe` or any other program (AC-E-04). It only talks to the engine API through the Docker SDK in `sandbox/engine.py`. The SDK's TLS connection is built from an `ssl.SSLContext` loaded with the encrypted key and the passphrase from Credential Manager. The decrypted key is never written to disk.

**Docker Desktop:** not needed and not used. If its WSL integration is enabled for `Ubuntu-24.04`, the runbook explains how to turn it off so the two engines do not clash. Uninstalling it is your choice and not required.

---

## 4. Gaps found in completed parts (reported, not silently fixed)

| Gap | Where | Proposal in E |
|---|---|---|
| `MODEL_LOCK` is per process; Spec §7.2 needs "never LLM and Docker together" across processes | `llm_gateway/gateway.py:42` | D-46 resource lease; the gateway takes it around each call |
| `CLAUDE.md` bans `docker` imports in agents, but `agents/ruff.toml` has no `docker` entry | `agents/ruff.toml` | Add the ban (import-linter rule 5 already forbids non-allowlisted imports, so no current exposure) |
| Doctor checks Docker but not WSL memory or images | `scripts/doctor.py:137-150` | Add both checks |
| `never_writable` includes `runs/`; sandbox evidence must be written there by the harness (not agents) | `config/guardrails/protected_paths.yaml` | Unchanged for agents; the sandbox writes through the harness evidence writer, as the executor already does for `runs/<run_id>/diffs` |
| The roadmap says "image cache (HC-18)", but HC-18 is rate limiting | roadmap §13 E | Treat as a wording slip: the image "cache" is the locally built, digest-locked images; rate limiting applies to the verification endpoint |

---

## 5. Files

| Area | Create | Modify |
|---|---|---|
| Harness | `packages/harness/src/devteam_harness/sandbox/{__init__,spec,engine,runner,recipes,guard,results,tools}.py` | `tools/registry.py` (allow `execute`), `tools/builtin.py` (register 3 tools), `guardrails/config.py` (`KNOWN_TOOLS`, `STAGE_CEILINGS`), `llm_gateway/gateway.py` (take resource lease), `llm_gateway/providers/ollama.py` (unload, `/api/ps`), harness `pyproject.toml` (`docker`) |
| Services | `packages/services/src/devteam_services/{integration_builder,migration_runner,test_runner,templates}/` | — |
| Core | contracts `sandbox.py`; ID prefixes; events; settings `SandboxSettings` | `contracts/__init__.py`, `config/settings.py`, `contracts/schemas/*` (generated) |
| State | migration `0007_sandbox`; models; repository functions | head-revision test expectations (2 tests, as in D: DV row) |
| Worker | `devteam_worker` verification job; startup container sweep | `worker.py` |
| API | `routers/sandbox.py`, schemas | `main.py` (router), `openapi.json`; security route-inventory test (new routes) |
| Web | `SandboxPage.tsx`, test | `App.tsx`, `client.ts`, `schema.d.ts` (generated) |
| Templates | `templates/target_apps/react_fastapi_sqlite/**`, `templates/target_apps/html_fastapi_sqlite/**` | — |
| Images | `sandbox/images/python_api/{Dockerfile,requirements.lock}`, `sandbox/images/node_web/{Dockerfile,package.json,package-lock.json}`, `sandbox/images/images.lock.json`, `sandbox/tools/devteam_static_check/`, `sandbox/tools/devteam_req/` | — |
| Config | `config/profiles/*` sandbox values | `config/defaults.yaml`, `config/guardrails/policies.yaml` |
| Scripts | `scripts/setup_wsl_docker.ps1` (run by you once), `scripts/build_sandbox_images.py`, `scripts/verify_templates.py` | `scripts/doctor.py` (WSL distribution, engine over TLS, not Docker Desktop, `.wslconfig` cap, images), `scripts/dev.ps1` (start and keep the WSL distribution open; status line) |
| Repo | — | `.dockerignore`, root `pyproject.toml` (import contracts, ruff/mypy/pytest excludes for `templates/` and `sandbox/`), `agents/ruff.toml`, `.github/workflows/ci.yml` (still deselects `docker`) |
| Docs | `docs/architecture/sandbox.md`, `docs/runbooks/docker-setup.md` (Docker Engine in WSL2, certificates, `.wslconfig`, image build, turning off Docker Desktop's WSL integration), `docs/adr/0008-sandbox-engine-connection.md` (D-48, D-50, D-51) | ADR-0007 (Accepted (b)), `.gitignore` (confirm `data/` covers `sandbox_tls/`), README, CHANGELOG, `docs/THIRD_PARTY_LICENSES.md`, `docs/architecture/contracts.md`, trackers, completion report |

---

## 6. Dependencies to add

| Package | Constraint | Licence | Reason |
|---|---|---|---|
| `docker` (Docker SDK for Python) | `>=7.1,<8` | Apache-2.0 | Engine API client (HC-14). Imported only by `sandbox/engine.py` |
| — transitively `requests` | (via docker) | Apache-2.0 | Used by the SDK only; import contract 7 still forbids our code from importing it outside providers |
| — `pywin32` (Windows only, via docker) | (via docker) | PSF-2.0 | Installed by the SDK on Windows; not used by our TLS connection |

`urllib3` and `certifi` are already present (certifi accepted under KI-026). Before adding: `uv lock`, `pip-audit`, licence inventory test update, and a check that no GPL-family licence enters. Images (§3.5) pull their own pinned packages; their licences are listed in `images.lock.json` and `docs/THIRD_PARTY_LICENSES.md` (target-app section). No paid service; no new npm dependency for the platform UI. The full free-tool inventory is in §12.

---

## 7. Tests mapped to acceptance criteria

| Test | Marker | AC |
|---|---|---|
| Spec builder: every container has network none, limits, non-root, read-only root, `cap_drop ALL`, no host mounts, no socket; property test (hypothesis) that no recipe/argument combination loosens a limit | unit | AC-E-02, AC-E-04 |
| Recipe argument validation; unknown recipe refused; shell metacharacters impossible (argv only); `tcp://` Docker host refused | unit, security | AC-E-02 |
| JUnit/coverage parser (valid, malformed, oversized; documents with DTD or entity declarations refused before parsing) | unit, security | — |
| Resource Guard: lease across two processes; LLM call waits while sandbox holds it and vice versa; stale lease takeover; low RAM → waiting for resources | integration | AC-E-02 |
| Docker absent (engine pointed at a non-existent pipe) → **Blocked**, no process started on host | integration | AC-E-02, AC-E-04 |
| Static scan: no `subprocess`/`os.system`/`exec`/`spawn` in non-test platform code; negative control with a planted file | security | **AC-E-04** |
| **Isolation suite (`docker`, real engine):** outbound TCP and DNS fail; timeout kill; memory limit → OOM recorded; fork bomb stopped by PID limit; write to root filesystem fails; host paths and home not visible; host environment canary not visible; container and volume removed after success, failure, timeout and cancel; leftover sweep; image digest mismatch refused | docker, security | **AC-E-02** |
| Each isolation test has a **negative control**: the same probe in a container built *without* that one control (test-only builder) succeeds, proving the test detects it | docker, security | AC-E-02 |
| Template self-tests offline for both templates → `TemplateVerification` `passed` | docker | **AC-E-01** |
| Manifest validation; protected-file checksum violation detected; `.env` rendering never writes secrets | unit | — |
| Migration Runner: upgrade → downgrade → upgrade on disposable SQLite; PostgreSQL mode refused with flag off; with flag on (only if enabled) container destroyed after | docker | — |
| **E2E-3:** API queues verification → worker → integration service → sandbox → result visible via API, with peak memory, under the `low_memory` profile | docker, slow | **AC-E-01, AC-E-03** |
| Migration `0007` upgrade/downgrade; append-only trigger; rehearsal on a copy of the real DB | integration | — |
| Import contracts (3 new) with planted violations; schema snapshots; OpenAPI sync | contract | — |
| Regression: full suite of Parts A–D, including the `llm` tests (now taking the resource lease) | all | Regression |

Docker tests run locally only (CI keeps deselecting `docker`, like `llm`). Coverage targets: ≥ 90% for `devteam_harness.sandbox` (harness), ≥ 80% for the services.

---

## 8. Security

- **No host execution path** (AC-E-04): static scan + no fallback branch in the runner + Docker-absent test.
- **Network:** `network_mode=none` on every container; there is no setting to change it. Images are built by a separate trusted script, the only step with network.
- **Supply chain:** base images pinned by digest; dependencies pinned with hashes; audit results recorded; digest checked before every run.
- **Untrusted output:** stdout, stderr, JUnit and coverage are data: size-capped, safely parsed, labelled untrusted when shown to agents later (Part C input guard), never written to logs, audit or traces.
- **Secrets:** none enter a container. The environment comes only from the recipe; a canary secret registered in the Secret Manager must never appear in container environment, files or outputs (test).
- **Docker daemon:** reached only on `127.0.0.1` with mutual TLS (D-50). Plain TCP, remote hosts and Docker Desktop's pipe are refused at config load, with tests. The client key is encrypted at rest and its passphrase is only in Credential Manager. The engine socket is never mounted into a container. A test checks that a connection without a client certificate is rejected by the engine (negative control: the same call with the certificate succeeds).
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
| Docker licensing (R4) | — | Closed by D-07 (b): Docker Engine (Apache-2.0) from Ubuntu's archive; Docker Desktop not used |
| WSL localhost forwarding or idle shutdown behaves differently on this WSL version | Engine unreachable from Windows | Verified in the E.1 spike; `dev.ps1` keeps a session open; failure is reported as **Blocked**, never bypassed. Free fallback: WSL `networkingMode=mirrored` |
| Ubuntu's `docker.io` lacks a needed feature | Rework | D-51 fallback to Docker's free apt repository, with your approval |
| Docker Hub limits anonymous image downloads (free, about 100 per 6 hours) | Image build delayed | Images are built rarely (2 base images); no account needed. If limited, wait and retry |
| Client certificate expires (2 years) | Blocked | Doctor warns 30 days ahead; the setup script re-issues |
| Memory: Docker VM + Ollama + browser on ≈5–6 GB free (R2) | Runs wait or OOM | Ollama unloaded before every run; WSL cap 4 GB (D-45); container 1.5 GB; peak recorded (AC-E-03) |
| `npm run build` for React needs more than 1.5 GB or more than 600 s on this CPU | AC-E-01 for react | Measure in E.4; raise the ceiling in `standard` only with evidence and your approval |
| Archive copy-in with a read-only root may need a writable volume layout that needs adjusting on Ubuntu's engine version | E.1 spike | D-48 fallback (b) |
| Base-image or package vulnerabilities found by the build-time audit | Image refused | Pin to fixed versions; record and report; never silently ignore |
| Long `docker` suite slows gates | Time | Kept out of the fast suite and CI; run at checkpoints and at the gate |
| Laptop sleep during long docker runs (KI-035) | Spurious failures | Same process-scoped keep-awake wrapper used in D |

---

## 11. Checkpoint breakdown and gate procedure

| Checkpoint | Exit criteria (internal) |
|---|---|
| E.1 | Docker Engine in WSL set up and reachable over mutual TLS (plain TCP and Docker Desktop refused, with tests); rulings recorded; dependency + licences + `pip-audit` clean; contracts + schemas; migration 0007 tested + rehearsal; settings; import contracts with planted violations; doctor checks; spike result recorded (ADR-0008); fast suite + static gates green; commit |
| E.2 | Spec builder, runner, guard, sweep; images built by the script and locked; isolation tests for network, timeout, memory, PIDs, root FS, cleanup, Docker-absent, lock; commit |
| E.3 | 10 recipes, 3 tools with policy grants, JUnit/coverage parser, Migration Runner (SQLite; PostgreSQL flag path); commit |
| E.4 | Both templates + manifests + `req` plugin; Integration/Build base; self-tests pass offline; worker job, API, UI; OpenAPI/types; web suite; commit |
| E.5 | Full isolation suite with negative controls; E2E-3; peak memory under `low_memory`; full regression A–D (incl. `llm`); security, dependency, licence, secret scans; docs, runbook, trackers, completion report; commit; **stop for G-E** |

Gate procedure: roadmap §17.1. After G-E approval: merge locally, `v0.5.0`, `part-e-approved`; push only with your separate approval.

---

## 12. Free and open-source inventory (AP-019)

Every item is free to use, needs no account or subscription, and runs locally. Paid items are excluded by rule and by checks.

| Item | Used for | Licence | Cost / account |
|---|---|---|---|
| WSL 2 (Microsoft, open source since 2025) and Ubuntu 24.04 | Linux VM for the engine | MIT (WSL); Ubuntu free (mostly GPL/MIT/BSD packages, used as an OS, not linked into our code) | Free; already installed |
| Docker Engine `docker.io` (Moby), `containerd`, `runc`, `docker-buildx` | Containers, image builds | Apache-2.0 | Free, Ubuntu archive |
| OpenSSL | Certificates (inside Ubuntu, set-up only) | Apache-2.0 | Free; already installed |
| Docker SDK for Python (`docker`), `requests`, `urllib3`, `pywin32` | Engine API client | Apache-2.0, Apache-2.0, MIT, PSF-2.0 | Free (PyPI) |
| Base images `python:3.12-slim`, `node:22-slim` (Debian based) | Sandbox images | PSF-2.0 / MIT plus Debian free packages | Free anonymous Docker Hub download; no account |
| Python packages in the `python_api` image: FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, HTTPX, pytest, pytest-cov, Ruff, Bandit | Template backend and checks | MIT, BSD-3, MIT, MIT, MIT, BSD-3, MIT, MIT, MIT, Apache-2.0 | Free (PyPI) |
| npm packages in the `node_web` image: React, React DOM, Vite, @vitejs/plugin-react, TypeScript, Vitest, jsdom | Template frontend and checks | MIT, MIT, MIT, MIT, Apache-2.0, MIT, MIT | Free (npm registry) |
| `pip-audit`, `npm audit` | Vulnerability check at image build | Apache-2.0; part of npm (Artistic-2.0) | Free public advisory databases (OSV, GitHub advisories); no account |
| Ollama, LangGraph, SQLite, FastAPI, React (platform, unchanged) | Existing platform | MIT and similar (inventory in `docs/THIRD_PARTY_LICENSES.md`) | Free |

**Explicitly not used:** Docker Desktop, Docker Hub accounts or paid registries, Docker Scout, Testcontainers Cloud, any cloud build or CI minutes, any paid AI or API. The `.github/workflows/ci.yml` inside each template is only a **file delivered** with the generated app; the platform never runs it.

**How "free only" is enforced:**

- the licence inventory test (Part D) is extended to the new Python packages, and fails on any licence not in the approved free list;
- `build_sandbox_images.py` records every package licence in each image and refuses unknown, proprietary or AGPL/GPL-family licences in image contents (a contract test re-checks `images.lock.json`);
- the doctor and the sandbox refuse Docker Desktop's engine, so a paid-tier product cannot be used by accident.

---

## Owner confirmation: ☑ D-07 (b) Docker Engine inside WSL2 (AP-019) · ☐ D-04 · ☐ D-08 · ☐ D-16 · ☐ D-24 · ☐ D-45 · ☐ D-46 · ☐ D-47 · ☐ D-48 · ☐ D-49 · ☐ D-50 · ☐ D-51 · ☐ container limit values (§3.2) · ☐ start implementation (after G-D)
