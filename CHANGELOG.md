# Changelog

One section per approved part (roadmap §8). Entries are added at gate approval; work awaiting approval is listed under *Unreleased*.

**Versioning:** SemVer, pre-1.0. Each approved part bumps the minor version (Part A = 0.1.0, B = 0.2.0, …, R = 0.18.0); fixes to an approved part bump the patch version. The accepted MVP is `1.0.0-mvp`. Git tags: `vX.Y.Z` plus `part-<letter>-approved` (roadmap §16.2).

## Unreleased — Part D: LangGraph Orchestration, Ten Agents & Head Agent Deterministic Core (implemented; awaiting G-D)

### Added (checkpoint D.1)

- New workspace packages `devteam-orchestration` (LangGraph; local-only tracing guard) and
  `devteam-worker` (skeleton; the worker is built in D.4). Dependencies `langgraph` 1.2.x and
  `langgraph-checkpoint-sqlite` 3.1.x (orchestration only)
- Orchestration contracts: closed `NodeName` list with kinds and subgraphs, exactly one agent node per
  role, the static allowed-transition table, `GateResume` (IDs only), `DispatchRecord`,
  `PlanValidationReport`, `RunMode` (stub runs, D-38); progress events (node started/finished, gate
  opened, run progress, metric sample); 81 exported JSON Schemas
- Migration `0005_orchestration`: run-control columns on `runs`; `spec_versions` (draft → frozen, guarded
  by triggers, D-39), `task_plans`, `run_jobs`, `worker_lease` (single row), `run_events`, `node_effects`
  (two-phase idempotency); repositories for each
- Settings section `orchestration` (recursion limit, worker polling, heartbeat, lease, crash-resume cap
  D-40, resource wait)
- Four new import-linter contracts (11 in total) and Ruff bans keeping LangGraph and orchestration out of
  agents and lower layers, each with a planted-violation test

### Added (checkpoint D.2)

- Head Agent deterministic core (no model): plan validator reporting every problem with a code,
  template planner, sequential scheduler, dispatcher with attempt-ledger keys, failure router rules table
  (F-10c refusals never retried, F-10d re-attempt bound, crash rule D-40)
- Service steps: honest stand-ins that can only report "not run / not evaluated / not built / not
  published" (`ServiceOutcome` contract), and the real Spec Freeze service (re-verifies the approval,
  freezes the spec version, audited, safe to repeat)
- Import contract: orchestration, the worker, the API and agents can never use the fake model provider
  (revised D-38: real AI providers only)
- `docs/THIRD_PARTY_LICENSES.md`: runtime licence inventory with MPL-2.0 obligations (`orjson`, `certifi`),
  checked by a test

### Added (D.2 completion; D-44)

- Direct local Ollama provider in the LLM Gateway: loopback only, proxies ignored, no redirects, answers
  constrained by JSON Schema, honest failures (unreachable or missing model = waiting for resources). No
  LiteLLM. Models per slot in `config/defaults.yaml` (`qwen2.5:3b`, `qwen2.5-coder:3b`, `qwen2.5:1.5b`)
- The ten agents' behaviours: each runs a genuine local model call through the executor and the gateway
  (minimal behaviour; real behaviour arrives in Parts H–O); model answer contracts (`contracts.drafts`)
- Migration `0006_agent_outputs` (validated outputs by task, append-only); certifi licence ruling recorded

### Added (checkpoint D.3)

- The `project_run` LangGraph graph: 37 nodes, edges generated from the transition table, three layers
  keeping Build behind approval (topology, build-entry guard, spec re-check in every Build/Delivery
  node), gates with `interrupt()`, SQLite checkpoints without pickle, effects ledger, diagram document

### Added (checkpoint D.4)

- `python -m devteam_worker`: single-worker lease, heartbeat with metrics, recovery after a crash (D-40),
  pause/resume/cancel at node boundaries, waiting for resources; started by `scripts/dev.ps1`
- Run-control API: start, list, read, pause, resume, cancel, gate view and decision (through the Approval
  Service), tasks, events (JSON and server-sent events), trace, history, metrics

### Added (checkpoint D.5)

- UI: runs on the project page, live Build Progress page with gate decisions, trace viewer, run history
- End-to-end test through the API and a real worker process in a loopback-only network jail

### Changed (checkpoint D.1)

- `RunState` contract v1 → **v2** (`run_mode`, `clarification_round_used`); schema files are now named
  by each model's own version (`RunState.v2.json`)

## 0.3.0 — Part C: Shared Contracts & Harness Core (G-C approved 2026-11-23)

### Added (checkpoint C.1)

- Shared contracts v1 (`devteam_core.contracts`): 22 domain records, `AgentTask`/`TaskPlan`, input/output
  contracts for all ten agents, tool, model-call, event and run-state contracts; 71 exported JSON Schemas
  with a snapshot check (`scripts/export_contract_schemas.py --check`).
- Contract-level enforcement: safe workspace paths, never-assume provenance, bound handoffs, no agent
  field for test results, verification or approval, human-only approvals, trust-labelled model messages.

### Added (checkpoint C.2)

- Deny-by-default policy engine (role x tool x path x stage) with audited denials and code-level ceilings that
  configuration cannot exceed; agent and service identities; input guardrails (data delimiting, secret
  refusal, injection flags) and output guardrails (forbidden claims, secret detection).
- Secrets in Windows Credential Manager only (`keyring`), no plaintext fallback; the sign-in key moved there
  under four checks (D-18a) and the old `data/api_token` file removed.

### Changed (checkpoint C.2)

- The API refuses to start if the credential store is unavailable or a plaintext key file still exists.
- Logs are redacted at the source as well as in the platform's own formatters.

### Added (checkpoint C.3)

- Controlled tool layer with five typed tools (no shell): registry, identity binding, policy, approval and
  budget checks, time limits, and an audit record for every call; Controlled File Service with Windows-aware
  path safety (junctions, symlinks, short names, hard links) and atomic writes with checksums and diffs.
- Tamper-evident audit log (hash chain, migration `0003_harness_core`); Part B entries covered without being
  changed. "Log intact" check on the Activity page, `GET /api/audit/verify`, `scripts/verify_audit.py`.
- Run and task state machines with an attempt ledger; illegal status changes are refused and recorded.

### Added (checkpoint C.4)

- Budget Manager with persisted usage counters for every F-9 limit (`config/budgets.yaml`), checked before
  each action; retry policy for F-10 (policy denials and exhausted budgets are never retried).
- Approval Service (`config/approvals.yaml`): human roles only, bound to run and spec version + checksum;
  forged, mismatched, stale, duplicate and out-of-band approvals are refused and audited.
- Centralised LLM Gateway (no LiteLLM) with a scripted fake provider: secret and delimiting checks before
  any call, one model call at a time, transient retries, structured-output validation, metadata-only audit.
- Prompt and skill registry interfaces (skills grant no permission); local-only OpenTelemetry tracing to
  SQLite with an attribute allowlist.
- Agent SDK (`AgentSpec`, ten-agent `AgentRegistry`, restricted `AgentContext`) and the Agent Executor
  lifecycle; the ten agent packages under `agents/` (declarations only).
- Agent isolation enforced by import-linter, Ruff banned-API and AST rules; `check_agent_count.py` strict by
  default. Migration `0004_execution`; `scripts/demo_part_c.py`.

## 0.2.0 — Part B: Project Intake, Document Extraction, Human IAM & Audit Foundation (G-B approved 2026-11-19)

### Added

- Projects (create, list, read, rename); per-project workspaces. No deletion yet (D-31, DV-006).
- BRD upload of PDF, DOCX, TXT and MD (D-22 (b)): filename, type, size, signature, zip-bomb, encryption and
  duplicate checks; a clear per-file result, so good files continue when others fail.
- Extraction (pdfplumber/pypdf, python-docx, charset-normalizer) into deterministic, referenced chunks
  (file · page · section · paragraph).
- Local sign-in key with an HttpOnly session cookie, loopback origins only, API rate limits, and an
  append-only activity log of every change and refused request (HC-15, HC-18, HC-20, HC-21 foundations).
- UI: sign-in, projects dashboard, project/upload, extraction review, activity.
- Alembic migration `0002_intake_iam_audit`; new packages `devteam_harness` and `devteam_services`.

## 0.1.0 — Part A: Foundation, Repository Conventions & Environment Benchmark (G-A approved 2026-11-19)

### Added

- uv workspace with three packages:
  - `devteam_core`: layered validated configuration; structured JSON logging with correlation fields and secret redaction; W3C trace context; IDs; clock; paths.
  - `devteam_state`: SQLite engine with WAL and foreign keys; Alembic baseline; online backup.
  - `devteam_api`: `/health`, `/version`, request-ID/trace middleware, OpenAPI export.
- React + TypeScript + Vite UI shell with a system status page and a typed client generated from OpenAPI.
- Scripts: `doctor`, `benchmark_models`, `check_agent_count` (skeleton), `backup_state`, `export_openapi`, `setup.ps1`, `dev.ps1`.
- Repository conventions: `CLAUDE.md`, `README.md`, `.gitignore`, `.gitattributes`, `.dockerignore`, `.env.example`, `.pre-commit-config.yaml`.
- CI workflow definitions: `ci.yml` and `security.yml`. These are not yet run on GitHub; no push has been authorised.
- ADR-0001…ADR-0007, the Part A plan, and the baseline model benchmark.
