# Part A — Foundation, Repository Conventions & Environment Benchmark — Plan

Roadmap ref: §13 Part A · Milestone: M0 · Gate: G-A · Working Plan ref: §20 Part A, HS-01

## Authorisation context

- AP-003 (2026-11-19): the owner authorised the start of implementation, using roadmap v2.0 as the default. The authorisation covers source/test/config files, dependency installation, documentation and tracker updates, and local tests.
- **Excluded by the owner:** Git commits and GitHub pushes, external publishing, destructive or irreversible infrastructure changes.
- The owner asked that routine, reversible decisions not wait for a reply. This plan is therefore **presented for review rather than pre-confirmed**. It is recorded as such in the tracker.

## Scope confirmation

**In scope (this part):**

| Item | Roadmap source |
|---|---|
| Root files: `CLAUDE.md`, `README.md`, `pyproject.toml` (uv workspace), `uv.lock`, `.python-version`, `.gitignore`, `.gitattributes`, `.dockerignore`, `.env.example`, `.pre-commit-config.yaml`, `CHANGELOG.md` | §8, §13 A |
| `packages/core` (`devteam_core`): config, logging with redaction, trace-context helpers, IDs, errors, paths, clock | §13 A, HS-01 |
| `packages/state` (`devteam_state`): SQLite engine (WAL, FK, busy timeout), Alembic baseline, online backup | §13 A, Plan §11 |
| `apps/api` (`devteam_api`): app factory, `/health`, `/version`, request-ID/trace middleware, OpenAPI export | §13 A |
| `apps/web`: React + TS + Vite shell, routing, typed client generated from the API's OpenAPI | §13 A |
| `config/defaults.yaml` and `config/profiles/{low_memory,standard,test}.yaml` | §7 |
| Scripts: `doctor.py`, `benchmark_models.py`, `check_agent_count.py` (skeleton), `backup_state.py`, `dev.ps1`, `setup.ps1`, `export_openapi.py` | §13 A |
| CI workflows `ci.yml`, `security.yml` | §7, §13 A |
| ADR-0001…0007 | §9.3 |
| Benchmark report `docs/benchmarks/<date>-baseline.{md,json}` | §13 A |

**Out of scope (later parts):**

- IAM, CORS, rate limits, uploads → B.
- Contracts, policy, tools, banned-API rules for `agents/**` → C.
- No `agents/` directory is created in A (the agent-count script runs in skeleton mode).

**Harness components:**

- HC-10: structured JSON logging, implemented.
- HC-11: trace-context propagation, W3C `traceparent` compatible. The OpenTelemetry exporter follows in C (D-30).
- HC-17: log redaction processor. The secret registry hook is a module-level set that `SecretsManager` (C) will feed.

## Decisions for this part

| ID | Status going in | Handling in Part A |
|---|---|---|
| D-26 uv workspace | Open | **Proceed on the recommended default (a).** Reversible. Owner approval still pending |
| D-33 existing `.git` | Open | Inspected read-only: empty repository (no commits). **Proceed on (a):** the workspace root is the repository root. No commits made (not authorised) |
| D-20 repo visibility / D-34 commit `.docx` | Open | **Deferred.** No GitHub operations are authorised, so neither decision is exercised in A. The `.docx` stays untracked |
| D-21 licence | Open — **owner ruling required** | No `LICENSE` file is created. Does not block A |
| D-07 Docker distribution | Open — **owner ruling required** | Docker Desktop is already installed (not running). `doctor.py` reports it. Blocks **Part E**, not A |
| D-35 LiteLLM (new) | Owner direction 2026-11-19 | Keep the roadmap: the platform gateway calls Ollama directly. No LiteLLM |
| D-36 Skills registry (new) | Owner direction 2026-11-19 | Versioned skills inside HC-16 (interface in C, implementation in F). Skills grant no permissions. Roadmap amendment proposed, not applied |

## Dependencies to add

All are resolved by `uv lock` / `npm install`. No versions are copied from elsewhere.

**Python runtime:**

| Package | Dependencies |
|---|---|
| core | pydantic, PyYAML, structlog, python-dotenv |
| state | SQLAlchemy, Alembic |
| api | FastAPI, uvicorn |

**Python dev group:** pytest, pytest-cov, hypothesis, httpx, ruff, mypy, import-linter, types-PyYAML, psutil, types-psutil, pre-commit.

**Web runtime:** react, react-dom, react-router-dom.

**Web dev:** vite, @vitejs/plugin-react, typescript, vitest, jsdom, @testing-library/react, @testing-library/jest-dom, openapi-typescript, eslint, typescript-eslint, eslint-plugin-react-hooks, @eslint/js, globals.

**System tools:** Gitleaks (installed with winget, user scope).

**Models:** `qwen2.5:3b` and `qwen2.5-coder:3b` pulled for the benchmark. These are the Spec §9.2 slot candidates.

## Tests mapped to acceptance criteria

| AC | Evidence planned |
|---|---|
| AC-A-01 | `scripts/setup.ps1` then the full local check list runs green |
| AC-A-02 | **Cannot be verified in A.** No GitHub push is authorised. The workflows are written, and their commands are run locally as an equivalent. Recorded as a known limitation |
| AC-A-03 | `benchmark_models.py`: 3 runs per model, medians, JSON + Markdown report |
| AC-A-04 | `doctor.py`: Ollama bound to loopback; `OLLAMA_MAX_LOADED_MODELS` / `OLLAMA_NUM_PARALLEL` / `OLLAMA_KEEP_ALIVE` reported. Setting user environment variables changes the owner's machine configuration, so it is **recommended, not done silently** |
| AC-A-05 | `uv lock` then `uv sync --locked` |
| AC-A-06 | CLAUDE.md checked against the roadmap §8 convention list |
| AC-A-07 | `gitleaks detect --no-git` over the working tree |

**Unit tests:**

- Config precedence and profiles.
- Loopback-only Ollama URL validation.
- Redaction: registered values, sensitive keys, token patterns, URL credentials.
- Log line IDs.
- Trace-context parse/format.
- ID generation and validation.
- `/health` and `/version`.
- Request-ID propagation.
- Agent-count script.
- Backup.

**Integration tests:**

- Alembic upgrade/downgrade on a temporary DB.
- API boot on a fresh DB.
- Web shell renders with a mocked `/health`.

**Smoke test:** `dev.ps1` starts the API and web; `/health` responds through the Vite proxy.

## Risks specific to this part

- **Low free RAM.** About 2 GB was free at session start. The benchmark may page or be slow. Measured numbers are reported as measured, together with the free RAM at the time.
- **No 7B benchmark.** A 7B model is benchmarked only if free RAM allows (Spec §9.3). Otherwise the skip and its reason are recorded.
- **Unverifiable criteria.** CI on GitHub (AC-A-02) and the clone-to-green walkthrough (AC-A-01) need a pushed repository. Both are deferred until the owner authorises Git/GitHub.
