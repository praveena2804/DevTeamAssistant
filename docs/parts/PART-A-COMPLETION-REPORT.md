# Part A — Foundation, Repository Conventions & Environment Benchmark — Completion Report

Roadmap ref: §13 Part A · Milestone: M0 · Gate: **G-A** · Date: 2026-11-19 · Authorisation: AP-003

## Summary

**What Part A delivers.** A working, convention-compliant foundation for the DevTeam AI platform:

- uv workspace with three packages: `devteam_core`, `devteam_state`, `devteam_api`;
- React + TypeScript + Vite UI shell;
- environment and maintenance tooling;
- CI workflow definitions;
- ADRs;
- a measured local-model benchmark.

**Harness components touched.** These are foundations only; the components themselves are built in later parts:

| Component | Part A work |
|---|---|
| HC-10 | Structured JSON logging with correlation fields |
| HC-11 | W3C trace-context propagation |
| HC-17 | Secret redaction with a registry that `SecretsManager` (Part C) will feed |

No agents exist yet, by design (roadmap §13 A: "Agents: none").

**Status of the evidence:**

- All Part A code is **implemented and tested locally**.
- Two items **cannot be fully verified** without owner action: AC-A-02 (GitHub CI) and AC-A-04 (Ollama environment variables). See below.

## Acceptance criteria

| AC | Result | Evidence |
|---|---|---|
| AC-A-01 Fresh clone → documented setup → all checks pass on the owner's laptop | **Partially verified.** Every documented check passes in this workspace (TR-0012, TR-0014, TR-0015, VR-002…VR-005). A literal *fresh clone* is impossible until a commit exists (DV-002) | Tracker §7, §8 |
| AC-A-02 CI green on the Part A PR | **Not verifiable.** No GitHub push or PR is authorised (AP-003). The same commands were run locally and are green | DV-002, KI-006 |
| AC-A-03 Benchmark report with measured numbers and a recommendation | **Pass.** See *Benchmark* below. The first run was invalid (task-design flaw); it is kept as evidence and was rerun | TR-0011, TR-0016, DV-005 |
| AC-A-04 Ollama localhost-only, one loaded model, one parallel request (via `doctor`) | **Partial.** Localhost-only verified (config validator + doctor). `OLLAMA_MAX_LOADED_MODELS` / `NUM_PARALLEL` / `KEEP_ALIVE` are **not set**; I did not change your user environment without consent | VR-006, KI-004 |
| AC-A-05 Fresh `uv lock`; `uv sync --locked` succeeds | **Pass** | VR-003 |
| AC-A-06 CLAUDE.md contains every roadmap §8 convention | **Pass** (manual checklist) | VR-008 |
| AC-A-07 No secrets in the repository | **Pass.** Gitleaks clean after triaging 9 false positives; a planted-token negative control was detected | VR-004 |

## Tests executed (actual)

| Suite | Command | Passed | Failed | Skipped |
|---|---|---|---|---|
| Python fast suite: unit + integration + contract + scripts | `uv run pytest -m "not llm and not docker and not slow and not acceptance" --cov` | 128 | 0 | 0 |
| Web | `npm run lint / typecheck / test / build` (apps/web) | 5 (Vitest) + 3 clean gates | 0 | 0 |
| Smoke | `./scripts/dev.ps1 -Smoke` | SMOKE OK | — | — |
| Static | ruff, ruff format, mypy strict, import-linter, agent-count | all clean | — | — |
| Security | gitleaks, npm audit, pip-audit | clean | — | — |

One failure occurred during development (TR-0003). It was a test-side encoding bug and was fixed; the failure is recorded in the tracker.

**No `docker`, `llm`, `slow` or `acceptance` tests exist yet.** Nothing was skipped.

## Coverage

95% total branch coverage over `devteam_core`, `devteam_state` and `devteam_api` (TR-0012). Every module is at 91% or above, except `openapi_export.py` at 67%: its write path is exercised by the script and the contract test rather than by unit tests.

## Security controls in this part

- **Secret redaction** on every log line, covering structlog and stdlib loggers. Three mechanisms:
  - registered secret values;
  - sensitive key names at any depth;
  - token patterns (GitHub, AWS, JWT, bearer, PEM, URL credentials, `key=value`).

  The canary test has a negative control proving it fails when redaction is removed (VR-009).
- **Local-only model endpoint** is enforced at config load: non-loopback URLs, other schemes and embedded credentials are rejected. This was pulled forward from Part F (DV-004).
- **Strict config:** unknown keys are rejected and settings are immutable.
- **Unsafe client request IDs** are replaced; trace headers are parsed defensively (property-tested).
- **The benchmark never executes model-generated code:** the code task is scored statically. A test proves a dangerous payload is rejected without running.
- **API binds 127.0.0.1** by default. The token, CORS and rate limits arrive in Part B, before any mutating endpoint.
- **Backups are taken before any migration** of an existing state DB.
- **Supply chain:** `uv sync --locked`, `npm ci`, pip-audit, npm audit and Gitleaks are wired into the workflows.

## Benchmark

Source: `docs/benchmarks/2026-11-19-baseline.md` (TR-0016), run 10:51–10:58 IST.

**Run conditions:**

- 3 runs per model, cold load each run;
- `num_ctx` 4096;
- 3.0 GB free RAM at start;
- no other heavy work running.

| Model | Slot | Structured (schema) | Code (static) | Load s | Tok/s | Structured task s | Peak Ollama RSS | Min free RAM |
|---|---|---|---|---|---|---|---|---|
| `qwen2.5:3b` | instruct | 3/3 | 3/3 | 4.5 | 13.2 | 23.6 | 2.1 GB | 1.2 GB |
| `llama3.2:3b` | instruct | 3/3 | 3/3 | 6.3 | 10.9 | 30.7 | 2.6 GB | **0.46 GB** |
| `qwen2.5-coder:3b` | coder | 3/3 | 3/3 | 3.5 | 12.5 | 28.6 | 2.1 GB | 1.6 GB |
| `qwen2.5:1.5b` | fallback | 3/3 | 3/3 | 2.2 | 23.3 | 18.7 | 1.2 GB | 2.7 GB |

**Recommendation (advisory; the owner decides):**

| Slot | Model | Reason |
|---|---|---|
| Instruct | `qwen2.5:3b` | Faster than `llama3.2:3b`, with about 400 MB less memory |
| Coder | `qwen2.5-coder:3b` | Passed every run |
| Fallback | `qwen2.5:1.5b` | Smallest passing model; first step of the fallback ladder |

**Not benchmarked:**

- **7B models:** free RAM did not allow it (Spec §9.3).
- **Embedding model:** deferred to Part F (KI-011).

**Limits of this evidence:**

- The quality tasks are smoke-level.
- The code task is checked statically and never executed.
- Real quality evidence comes from the per-agent evaluation sets (HC-09) from Part H onward.

## Files created / modified

The exact per-file register is in tracker CH-0004…CH-0024 (§5, §6).

**Areas touched:**

- root repository files and `.github/workflows/`;
- `config/`;
- `packages/core`, `packages/state`;
- `apps/api`, `apps/web`;
- `scripts/`;
- tests;
- `docs/adr`, `docs/parts`, `docs/architecture`, `docs/benchmarks`.

**Pre-existing files.** The planning documents and the specification were not modified, apart from this tracker.

## Deviations from plan

| ID | Deviation |
|---|---|
| DV-001 | The plan was not pre-confirmed |
| DV-002 | No GitHub, PR or CI run |
| DV-003 | D-35/D-36 are not yet in the roadmap body |
| DV-004 | Ollama loopback validation was pulled forward from Part F |
| DV-005 | The benchmark task was redesigned after the first, invalid run |

## Known issues & limitations

| ID | Issue |
|---|---|
| KI-004 | Ollama environment variables not set |
| KI-005 | Low free RAM: about 2 GB, down to about 450 MB with a 3B model loaded |
| KI-006 | CI has never run on GitHub |
| KI-007 | Docker engine stopped; D-07 open |
| KI-008 | Starlette test-client deprecation warning |
| KI-009 | TypeScript pinned to 5.9 by toolchain peers |
| KI-010 | Gitleaks needs a new shell to appear on `PATH` |
| KI-011 | Embedding benchmark deferred to Part F |

## Decisions

| Status | Decisions |
|---|---|
| Ruled by owner direction | D-35: no LiteLLM; D-36: skills inside HC-16 |
| Proceeding on recommended default (approval pending) | D-26 uv workspace; D-33 reuse the empty `.git` |
| Requiring your ruling | D-07 Docker licensing/distribution (blocks Part E); D-21 licence |
| Deferred (no GitHub use) | D-20, D-34 |

## Demonstration notes

- `./scripts/dev.ps1` opens the UI at http://127.0.0.1:5173. It shows **Healthy** with the `state_db` check, version and schema revision.
- `uv run python scripts/doctor.py` gives the environment report.
- `docs/benchmarks/2026-11-19-baseline.md` has the model measurements.

## Approval requested: G-A

Please review and decide:

1. **Approve gate G-A**, or request changes.
2. **Model selection** from the benchmark recommendation (Working Plan §20 A [APPR]).
3. **Rulings:** D-07, D-21, and confirmation of D-26/D-33.
4. **Optional:** set the three Ollama environment variables (README), and authorise Git commits / GitHub repository creation so that AC-A-01/02 can be verified.
