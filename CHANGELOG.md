# Changelog

One section per approved part (roadmap §8). Entries are added at gate approval; work awaiting approval is listed under *Unreleased*.

**Versioning:** SemVer, pre-1.0. Each approved part bumps the minor version (Part A = 0.1.0, B = 0.2.0, …, R = 0.18.0); fixes to an approved part bump the patch version. The accepted MVP is `1.0.0-mvp`. Git tags: `vX.Y.Z` plus `part-<letter>-approved` (roadmap §16.2).

## Unreleased — Part C: Shared Contracts & Harness Core (in progress)

### Added (checkpoint C.1)

- Shared contracts v1 (`devteam_core.contracts`): 22 domain records, `AgentTask`/`TaskPlan`, input/output
  contracts for all ten agents, tool, model-call, event and run-state contracts; 71 exported JSON Schemas
  with a snapshot check (`scripts/export_contract_schemas.py --check`).
- Contract-level enforcement: safe workspace paths, never-assume provenance, bound handoffs, no agent
  field for test results, verification or approval, human-only approvals, trust-labelled model messages.

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
