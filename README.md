# DevTeam AI

DevTeam AI is a local-first, BRD-driven software development platform.

1. A user uploads a Business Requirements Document (BRD).
2. The platform analyses the BRD, checks it against a capability catalog, asks one round of clarifying questions, and asks the user to approve a precise summary.
3. A supervised team of **exactly ten AI agents** then designs, generates, tests, repairs, documents and packages the requested application.
4. The agents work inside a central security and execution **harness**. Models run locally on Ollama; no paid service is used.

> **Current state: Part A (Foundation) is under development, awaiting gate G-A.**
> Only the foundation exists so far:
> - configuration, logging and trace context;
> - a SQLite state database;
> - the API with `/health` and `/version`;
> - the web shell;
> - environment tooling.
>
> No agents or harness components beyond logging and redaction exist yet. See `docs/IMPLEMENTATION_TRACKER.md` for the authoritative status.

## Documentation

| Document | Purpose |
|---|---|
| `DevTeam_AI_Master_Development_Specification_v2.docx` | Authoritative product specification |
| `docs/IMPLEMENTATION_ROADMAP.md` | What is built and when: 18 parts (A–R), 6 milestones, harness components HC-01…HC-21 |
| `docs/DETAILED_WORKING_PLAN.md` | How each part is executed |
| `docs/IMPLEMENTATION_TRACKER.md` | What has actually happened (changes, tests, decisions, approvals) |
| `docs/architecture/HARNESS_ASSESSMENT.md` | Agent and harness architecture assessment |
| `docs/adr/` | Architecture decision records |
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
apps/api/        FastAPI platform API (devteam_api): /health, /version
apps/web/        React + TypeScript + Vite UI shell (typed client generated from apps/api/openapi.json)
packages/core/   devteam_core: config, structured logging + redaction, trace context, IDs, errors, paths
packages/state/  devteam_state: SQLite engine (WAL), Alembic migrations, online backups
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
| Docker | any | required from Part E; distribution choice pending decision D-07 |

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
```

## Configuration

**Precedence:** `config/defaults.yaml` < `config/profiles/<profile>.yaml` < `.env` < process environment.

- Variables use the form `DEVTEAM_<SECTION>__<KEY>`, for example `DEVTEAM_LOGGING__LEVEL=DEBUG`.
- Copy `.env.example` to `.env` for local overrides; it holds placeholders only.
- Unknown keys and a non-loopback Ollama URL are rejected at startup.

## Development

```powershell
./scripts/dev.ps1                                   # API on http://127.0.0.1:8000, UI on http://127.0.0.1:5173
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
```

## Troubleshooting

| Problem | What to do |
|---|---|
| `doctor` warns about low free RAM | Close applications; the platform runs one model at a time |
| Ollama not reachable | Start Ollama; the URL must be `127.0.0.1`/`localhost` |
| Docker engine not running | Needed only from Part E |
| `gitleaks` not found right after `winget install` | Open a new terminal so `PATH` refreshes |
| API reports `degraded` | The state DB schema is behind; restart the API (it backs up, then migrates), or run `uv run alembic -c packages/state/alembic.ini upgrade head` |
