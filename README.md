# DevTeam AI

DevTeam AI is a local-first, BRD-driven software development platform.

1. A user uploads a Business Requirements Document (BRD).
2. The platform analyses the BRD, checks it against a capability catalog, asks one round of clarifying questions, and asks the user to approve a precise summary.
3. A supervised team of **exactly ten AI agents** then designs, generates, tests, repairs, documents and packages the requested application.
4. The agents work inside a central security and execution **harness**. Models run locally on Ollama; no paid service is used.

> **Current state: Parts A, B, C and D approved (v0.4.0). Part D runs the ten agents on the local Ollama; building, testing and publishing are still stand-ins.**
> What exists:
> - configuration, logging, trace context and secret redaction (Part A);
> - a SQLite state database with migrations and automatic backups (Part A);
> - projects, BRD upload (PDF, DOCX, TXT, MD) with validation, extraction into referenced text pieces,
>   and an extraction review screen (Part B);
> - local sign-in key, rate limits and an append-only activity log (Part B; harness HC-15, HC-18, HC-20, HC-21
>   foundations).
>
> - Part D: trial runs. The ten agents make real calls to the local Ollama model; building, testing, packaging
>   and publishing are stand-ins that run nothing, so **nothing is built, tested or verified yet**.
> - Part E (built, awaiting approval G-E): a secure Docker sandbox (Docker Engine inside WSL2, mutual TLS, no
>   network, locked images) with ten fixed build and test recipes, and two starter kits checked in the sandbox.
>   The run graph does not use the sandbox yet; project runs still use the Part D stand-ins.
>
> See `docs/IMPLEMENTATION_TRACKER.md` for the authoritative
> status and `docs/PROJECT_PROGRESS_TRACKER.md` for a plain-English run history.

## Documentation

| Document | Purpose |
|---|---|
| `DevTeam_AI_Master_Development_Specification_v2.docx` | Authoritative product specification |
| `docs/IMPLEMENTATION_ROADMAP.md` | What is built and when: 18 parts (A–R), 6 milestones, harness components HC-01…HC-21 |
| `docs/DETAILED_WORKING_PLAN.md` | How each part is executed |
| `docs/IMPLEMENTATION_TRACKER.md` | What has actually happened (changes, tests, decisions, approvals) |
| `docs/PROJECT_PROGRESS_TRACKER.md` | Plain-English history: one short paragraph per development run |
| `docs/architecture/MODEL_ROUTING.md` | Model-routing audit and proposed design (D-06, D-37) |
| `docs/architecture/HARNESS_ASSESSMENT.md` | Agent and harness architecture assessment |
| `docs/adr/` | Architecture decision records |
| `docs/THIRD_PARTY_LICENSES.md` | Runtime dependency licences and the MPL-2.0 obligations |
| `docs/architecture/orchestration.md` | The run graph (generated diagram) |
| `docs/runbooks/worker.md` | Starting, stopping and recovering the run worker |
| `docs/runbooks/docker-setup.md` | Setting up the sandbox engine, certificates and images |
| `docs/architecture/sandbox.md` | How sandbox runs work and which controls apply |
| `CLAUDE.md` | Engineering rules for coding agents |

## Architecture

Below is the target architecture. Parts are delivered incrementally.

```text
apps/web (React+TS) ──HTTP──► apps/api (FastAPI) ──► apps/worker (LangGraph run worker)
                                                            │
                     ┌──────────────── packages/harness (HC-01…HC-21) ─────────────────┐
                     │ executor · policy · IAM · LLM gateway → Ollama (localhost only)   │
                     │ tools · Docker sandbox · approvals · audit · tracing · secrets   │
                     └───────────────────────────────────────────────────────────────────┘
                                   ▲                                   ▲
                       agents/ (exactly 10)              packages/services (deterministic)
```

**The ten agents:**

1. BRD Analyst
2. Requirements Finalizer
3. Head Agent
4. Architecture Agent
5. Database Developer
6. Backend Developer
7. Frontend Developer
8. Testing & Validation Agent
9. Debugger & Code Reviewer
10. Documentation & Reporting Agent

## Repository structure (current)

```text
apps/api/        FastAPI platform API (devteam_api): /health, /version (public); /api/* (sign-in required)
apps/web/        React + TypeScript + Vite UI: sign-in, projects, upload, extraction review, activity
packages/core/   devteam_core: config, structured logging + redaction, trace context, IDs, errors, paths
packages/state/  devteam_state: SQLite engine (WAL), models, repositories, Alembic migrations, backups
packages/harness/ devteam_harness: HC-15 local IAM + token, HC-18 rate limits, HC-20 upload validation,
                 HC-21 audit (foundations only)
packages/services/ devteam_services: workspace manager, document extraction + chunking, intake (not agents)
packages/orchestration/ devteam_orchestration: LangGraph run graph, guards, gates, runtime (Ollama), run commands
apps/worker/     devteam_worker: the run worker (lease, heartbeat, crash recovery, template verifications)
packages/harness/.../sandbox/ the Docker sandbox: engine (mutual TLS), runner, resource guard, ten recipes
sandbox/         locked sandbox images (Dockerfiles, dependency locks, licence policy) and in-container tools
templates/target_apps/ starter kits react_fastapi_sqlite and html_fastapi_sqlite (target applications)
agents/          the ten agents: declaration (spec.py) + behaviour (behaviour.py)
config/          defaults.yaml + profiles (low_memory, standard, test)
scripts/         doctor, benchmark, agent-count check, backup, OpenAPI export, setup/dev PowerShell
tests/           cross-package integration, contract and script tests
docs/            roadmap, plan, tracker, ADRs, part plans/reports, benchmarks
```

## Windows installation

**Prerequisites:**

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 | |
| uv | ≥ 0.5 | |
| Node.js | 22 LTS | npm included |
| Git | any | |
| Ollama | any | serves models on localhost only |
| Gitleaks | any | secret scanning |
| WSL2 + Ubuntu 24.04 | — | Docker Engine runs inside WSL (D-07 (b)); never Docker Desktop. Set up with `docs/runbooks/docker-setup.md` |

**Recommended Ollama settings** (user environment variables; restart Ollama afterwards, Spec §9.3):

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "1m", "User")
```

**Set up and check the environment:**

```powershell
./scripts/setup.ps1            # uv sync --locked, npm ci, then runs the doctor
uv run python scripts/doctor.py
uv run python scripts/setup_wsl_docker.py      # once: sandbox engine + certificates (runbook docker-setup.md)
uv run python scripts/build_sandbox_images.py  # once, and after image changes: the only step with network
```

## Configuration

**Precedence:** `config/defaults.yaml` < `config/profiles/<profile>.yaml` < `.env` < process environment.

- Variables use the form `DEVTEAM_<SECTION>__<KEY>`, for example `DEVTEAM_LOGGING__LEVEL=DEBUG`.
- Copy `.env.example` to `.env` for local overrides; it holds placeholders only.
- Unknown keys, a non-loopback API host, non-loopback allowed origins and a non-loopback Ollama URL are rejected
  at startup.
- Upload limits (`uploads:`), sign-in settings (`auth:`) and rate limits (`ratelimit:`) live in
  `config/defaults.yaml`. Approved defaults: 20 MB per file, 10 files per upload, 300 PDF pages.

## Development

```powershell
./scripts/dev.ps1                                   # API on http://127.0.0.1:8000, UI on http://127.0.0.1:5173, run worker;
                                                    # prints and opens the private sign-in link
uv run python -m devteam_api.signin_link            # print the sign-in link again
uv run python tests/fixtures/brds.py data/sample_brds  # write sample BRDs (good and broken) for a demo
uv run python scripts/export_openapi.py             # after API changes, then:
npm --prefix apps/web run gen:api                   # regenerate the typed client
uv run python scripts/backup_state.py               # back up data/*.db
uv run python scripts/benchmark_models.py --runs 3  # local model benchmark (writes docs/benchmarks/)
```

## Tests and checks

```powershell
uv run pytest -m "not llm and not docker and not slow and not acceptance"
uv run ruff check . ; uv run ruff format --check . ; uv run mypy packages apps scripts
uv run lint-imports ; uv run python scripts/check_agent_count.py
npm --prefix apps/web run lint ; npm --prefix apps/web run typecheck ; npm --prefix apps/web test ; npm --prefix apps/web run build
gitleaks dir . --no-banner
uv run pytest tests/sandbox -m docker   # real sandbox tests; needs the WSL engine (add -m "docker and slow" for the long ones)
```

## Troubleshooting

| Problem | What to do |
|---|---|
| `doctor` warns about low free RAM | Close applications; the platform runs one model at a time |
| Ollama not reachable | Start Ollama; the URL must be `127.0.0.1`/`localhost` |
| Sandbox work is Blocked: engine not reachable | Start `./scripts/dev.ps1` (it keeps WSL open) or run `wsl -d Ubuntu-24.04 --exec sleep infinity` in a separate window. Work never runs on the laptop instead. See `docs/runbooks/docker-setup.md` |
| Sandbox work waits with `resource.low_memory` | WSL is holding file cache (KI-039); see the runbook's troubleshooting table |
| `gitleaks` not found right after `winget install` | Open a new terminal so `PATH` refreshes |
| API refuses to start: "A plaintext sign-in key file still exists" | Run `uv run python scripts/migrate_api_token.py` (or just `./scripts/dev.ps1`, which runs it). It moves the old `data/api_token` into Windows Credential Manager under four checks and deletes the file only if all pass |
| API refuses to start: "no secure OS credential store is available" | The key can only live in Windows Credential Manager; there is no file fallback. Check that the `PYTHON_KEYRING_BACKEND` environment variable is not set |
| UI says "You are not signed in" | Open the sign-in link printed by `scripts/dev.ps1` (or run `uv run python -m devteam_api.signin_link`). The key is stored in Windows Credential Manager (entry `devteam-ai`), never in a file; never share it |
| A run waits with "waiting for resources" | Ollama is not running or memory is low. Start Ollama; the run continues by itself. See `docs/runbooks/worker.md` |
| The worker refuses to start: "configured local models are not installed" | Install them, e.g. `ollama pull qwen2.5:3b` (models are listed in `config/defaults.yaml`); the platform never downloads models itself |
| A large PDF takes a long time to upload | Expected for now: reading happens during the upload (≈40 s for 300 pages on this laptop). Background reading arrives with the worker (Part D) |
| `npm test` fails with "Vitest failed to find the current suite" | Run it from PowerShell or a path that starts with an upper-case `C:`; a lower-case drive letter loads Vitest twice |
| API reports `degraded` | The state DB schema is behind; restart the API (it backs up, then migrates), or run `uv run alembic -c packages/state/alembic.ini upgrade head` |
