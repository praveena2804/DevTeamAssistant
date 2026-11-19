# DevTeam AI — Implementation Tracker

| Field | Value |
|---|---|
| Document | `docs/IMPLEMENTATION_TRACKER.md`. The permanent record of **what has actually happened** |
| Scope reference | `docs/IMPLEMENTATION_ROADMAP.md` (what and when) · `docs/DETAILED_WORKING_PLAN.md` (how) |
| Time zone | All timestamps are local time, IST (UTC+05:30) |
| Last updated | 2026-11-19 14:35 IST |

> **Mandatory rule.** Every time a project file is created, modified or deleted during authorised development, this tracker is updated **in the same session**:
> - Planned work is never recorded as completed.
> - Tests are never recorded as passed unless they were actually executed, with the command and result recorded.
> - This document must always reflect the real repository state.

---

## 1. Current Status Summary

| Item | Value |
|---|---|
| Current milestone | **M0 Foundation** |
| Current part | **Part B: In progress** (G-A approved 2026-11-19 14:30, AP-005) |
| Overall status | Part A implemented and locally validated. Completion report: `docs/parts/PART-A-COMPLETION-REPORT.md` |
| Last completed activity | 2026-11-19 12:50 (AP-004): model-routing audit (`docs/architecture/MODEL_ROUTING.md`), Part B plan drafted (`docs/parts/PART-B-PLAN.md`), simple-language tracker created (`docs/PROJECT_PROGRESS_TRACKER.md`). Documentation only, no code |
| Code written | Yes, Part A scope only: `devteam_core`, `devteam_state`, `devteam_api`, `apps/web` shell, scripts, tests |
| Dependencies installed | Python (uv, `uv.lock`), npm (`package-lock.json`), Gitleaks (winget, user scope), Ollama models `qwen2.5:3b` and `qwen2.5-coder:3b` |
| Git / GitHub operations | **None.** No commits or pushes (excluded by AP-003). The existing empty `.git` was inspected read-only |
| Tests executed | TR-0001…TR-0017. Final: pytest 128 passed / 0 failed / 0 skipped (95% coverage); Vitest 5 passed; smoke OK |
| Blocking items | G-A owner approval. For Part E: D-07. Unverifiable until Git is authorised: AC-A-01 (fresh clone), AC-A-02 (CI) |
| Next authorised action | **None.** Wait for the owner's decisions on G-A **and** the Part B plan. Part B implementation must not start until both are explicitly approved |

---

## 2. Part Status Board

**Status values:**

| Status | Meaning |
|---|---|
| Not started | No work done |
| Planning | `PART-X-PLAN.md` in progress |
| Plan confirmed | Owner confirmed the part plan |
| In progress | Implementation under way |
| In validation | Tests and acceptance checks running |
| Awaiting approval | Evidence package presented |
| Changes requested | Owner requested rework |
| Approved | Gate approved; merged and tagged |

| Part | Name | Milestone | Status | Gate | Plan confirmed | Approved on | Tag |
|---|---|---|---|---|---|---|---|
| A | Foundation, Repository Conventions & Environment Benchmark | M0 | **Approved** | G-A | Retrospectively, at G-A (DV-001) | 2026-11-19 (AP-005) | `part-a-approved`, `v0.1.0` (local) |
| B | Intake, Extraction, Human IAM & Audit Foundation | M0 | **In progress** | G-B | 2026-11-19 (AP-005) | — | — |
| C | Shared Contracts & Harness Core | M1 | Not started | G-C | — | — | — |
| D | LangGraph Orchestration, Ten Stub Agents & Head Agent Deterministic Core | M1 | Not started | G-D | — | — | — |
| E | Isolated Execution Sandbox & Target-App Templates | M1 | Not started | G-E | — | — | — |
| F | LLM Gateway, Model Routing, Context & Memory, and RAG | M1 | Not started | G-F | — | — | — |
| G | Capability Catalog & Feasibility Gate | M2 | Not started | G-G | — | — | — |
| H | BRD Analyst | M2 | Not started | G-H | — | — | — |
| I | Requirements Finalizer, Clarification, Approval & Spec Freeze | M2 | Not started | G-I | — | — | — |
| J | Head Agent Planner & Architecture Agent | M3 | Not started | G-J | — | — | — |
| K | Database Developer & Backend Developer | M3 | Not started | G-K | — | — | — |
| L | Frontend Developer & Integration/Build Service | M3 | Not started | G-L | — | — | — |
| M | Testing & Validation Agent & Evidence Capture | M4 | Not started | G-M | — | — | — |
| N | Debugger & Code Reviewer, Repair Loop & Completion Gate | M4 | Not started | G-N | — | — | — |
| O | Documentation & Reporting Agent & Packager | M4 | Not started | G-O | — | — | — |
| P | GitHub Delivery for Generated Applications | M4 | Not started | G-P | — | — | — |
| Q | Change Requests (+ Switch-Database Extension Point) | M5 | Not started | G-Q | — | — | — |
| R | Hardening, Security Validation & MVP Acceptance | M5 | Not started | G-R | — | — | — |

**Agent implementation status:** none of the ten agents are implemented.

| Agent | Stub (D) | Real implementation |
|---|---|---|
| BRD Analyst | — | — (H) |
| Requirements Finalizer | — | — (I) |
| Head Agent | — | Core — (D); LLM planner — (J) |
| Architecture Agent | — | — (J) |
| Database Developer | — | — (K) |
| Backend Developer | — | — (K) |
| Frontend Developer | — | — (L) |
| Testing & Validation Agent | — | — (M) |
| Debugger & Code Reviewer | — | — (N) |
| Documentation & Reporting Agent | — | — (O) |

**Harness component status:** HC-01…HC-21 are not built yet. Part A delivered only foundations: HC-10 structured logging (HS-01, implemented and tested), HC-11 trace-context propagation (HS-01, implemented and tested; OpenTelemetry exporter in C), and HC-17 log redaction (HS-01, implemented and tested; SecretsManager in C). The planned part for each component is in roadmap §12.

---

## 3. Approvals Log

Only explicit owner approvals are recorded here.

| Approval ID | Date/Time | Item approved | Approved by | Evidence (message/PR ref) | Notes |
|---|---|---|---|---|---|
| AP-001 | 2026-11-18 | Instruction to analyse the specification and produce a planning roadmap (no development) | Owner | Session instruction #1 | Authorised planning only |
| AP-002 | 2026-11-18 | Authorisation to create exactly three Markdown files in `docs/` (roadmap, tracker, working plan) | Owner | Session instruction #2 | No code, installs, Git or GitHub authorised |
| AP-003 | 2026-11-19 04:50 | **Start of development.** Authorises: implementation; source, test and config files; dependency installation; documentation and tracker updates; local tests. Instruction: use roadmap v2.0 as the default and do not wait for routine, reversible decisions | Owner | Session instruction "START DEVELOPMENT" (2026-11-19) | **Excludes:** Git commits and GitHub pushes; external publishing; production deployment; destructive or irreversible infrastructure changes. **Owner directions in the same instruction:** D-35 (no LiteLLM without asking; gateway calls Ollama directly); D-36 (versioned Skills Registry inside prompt/skill management; skills grant no permissions) |
| AP-004 | 2026-11-19 12:44 | Documentation tasks: simple-language tracker `docs/PROJECT_PROGRESS_TRACKER.md` (permanent, updated every part), model-routing audit, Part B plan | Owner | Instruction "Model Routing Audit, Part B Planning, and Simple-Words Progress Tracker" | **Not** an approval of G-A, of the Part B plan, of the model selection, or of Part B implementation. Git/publishing still excluded |
| AP-005 | 2026-11-19 14:30 | **Gate G-A approved.** Part B plan confirmed as recommended, with rulings: D-22 (b) MD accepted; D-31 deletion excluded from Part B; upload limits 20 MB/file, 10 files/upload, 300 PDF pages; D-11 (a), D-15 (b), D-29 (a) as in the plan; D-37 to be included in the architecture and implementation plan. **Local Git commits authorised** for completed milestones, with a versioning scheme (short messages, max two lines) | Owner | Owner message 2026-11-19 "I approve G-A and the Part B plan as recommended" | **Still pending:** model selection (owner will approve later); every other open decision must be asked when needed, never assumed. **Not authorised:** push to GitHub, publishing, deployment |

*Roadmap v2.0: adopted as the working default under AP-003. An explicit formal "roadmap approved" statement has not been given, so it is recorded as adopted, not approved.*

---

## 4. Decision Log

The full decision descriptions are in roadmap §20. This log tracks status only.

| ID | Topic | Status | Ruling | Date | Affected docs/sections |
|---|---|---|---|---|---|
| D-01 | Feasibility decision point | Open | — | — | Roadmap §6.5, §13 G; Plan §20 G |
| D-02 | Coverage denominator | Open | — | — | Roadmap §13 G |
| D-03 | Approval-screen edit behaviour | Open | — | — | Roadmap §13 I |
| D-04 | Target-app auth patterns | **Open, owner ruling required** | — | — | Roadmap §13 E/K |
| D-05 | Deterministic vs LLM share (Head/Architect) | Open | — | — | Roadmap §13 J |
| D-06 | Per-role model overrides | Open (recommended (b); audited 2026-11-19, see `docs/architecture/MODEL_ROUTING.md`) | — | — | Roadmap §13 F |
| D-07 | Docker distribution / licensing | **Open — owner ruling required; blocks Part E (not A)** | — (observed 2026-11-19: Docker Desktop 29.6.2 already installed, engine stopped; see ADR-0007) | — | Roadmap §9, §13 A/E; ADR-0007 |
| D-08 | Offline sandbox vs installs/audits | Open | — | — | Roadmap §13 E/N |
| D-09 | Frontend behaviour verification | Open | — | — | Roadmap §13 L/N |
| D-10 | Test-maintenance approver | Open | — | — | Roadmap §13 N |
| D-11 | Platform UI login | **Ruled (AP-005)** | (a) no login; loopback + per-install token | 2026-11-19 | Roadmap §13 B; PART-B-PLAN §2 |
| D-12 | Eval thresholds | Open (proposed per agent part) | — | — | Roadmap §13 H… |
| D-13 | Folder layout | **Superseded by D-26** | — | 2026-11-18 | Roadmap §20 |
| D-14 | Stable ID scope | Open | — | — | Roadmap §13 H/Q |
| D-15 | Platform-UI E2E automation | **Ruled (AP-005)** | (b) API E2E + manual UI demo until R | 2026-11-19 | Roadmap §15 |
| D-16 | PostgreSQL Docker validation | Open | — | — | Roadmap §13 E/K |
| D-17 | Two-stage Completion Gate | Open | — | — | Roadmap §13 N/O |
| D-18 | Secret store / GitHub auth | Open | — | — | Roadmap §13 C/P |
| D-19 | Pages eligibility | Open | — | — | Roadmap §13 P |
| D-20 | Platform repo visibility | **Open — deferred** (no GitHub use authorised; not exercised in A) | — | — | Roadmap §18.1 |
| D-21 | Platform licence | **Open — owner ruling required** (no `LICENSE` file created; does not block A) | — | — | Roadmap §8 |
| D-22 | MVP supporting-file types | **Ruled (AP-005)** | (b) PDF, DOCX, TXT **and MD** (MD read as plain text). Deviates from Spec §5.1 "PDF, DOCX, TXT initially" by owner decision | 2026-11-19 | Roadmap §13 B, §20; Spec §5.1 |
| D-23 | DB test ownership | Open | — | — | Roadmap §13 K |
| D-24 | Integration/Build Service classification | Open | — | — | Roadmap §4.2 |
| D-25 | BRD_ANALYSIS.md ownership | Open | — | — | Roadmap §13 H/O |
| D-26 | uv workspace with per-agent packages | **Proceeding on recommended default (a); owner confirmation pending** (G-A approval covered the implementation, not the explicit ruling) | (a) uv workspace — implemented in Part A (reversible until Part C adds agent packages) | 2026-11-19 | Roadmap §7, §8; ADR-0005 |
| D-27 | Parallel execution policy | Open | — | — | Roadmap §6.6 |
| D-28 | LLM response cache | Open | — | — | Roadmap §13 F |
| D-29 | Local API authentication | **Ruled (AP-005)** | (a) per-install token, HttpOnly cookie for UI, loopback, CORS (PART-B-PLAN §5.3) | 2026-11-19 | Roadmap §13 B |
| D-30 | Tracing implementation | Open | — | — | Roadmap §5.1 HC-11 |
| D-31 | Project deletion/retention | **Partially ruled (AP-005)**: deletion **excluded from Part B**; final ruling on (a)/(b) still open, to be asked when deletion is planned | — | 2026-11-19 | Roadmap §13 B ("projects CRUD" without delete, DV-006) |
| D-32 | Part C internal staging vs split | Open | — | — | Roadmap §13 C |
| D-33 | Pre-existing `.git` in workspace | **Proceeding on recommended default (a); owner approval pending** | Inspected read-only 2026-11-19: branch `master`, **zero commits**, no remotes → workspace root used as the repository root. No commits made (not authorised) | 2026-11-19 | Roadmap §18.1; Plan §16.1 |
| D-34 | Commit specification .docx | **Open — deferred** (no commits authorised) | — | — | Roadmap §18.1 |
| D-35 | LiteLLM in the LLM Gateway (new) | **Ruled by owner direction (AP-003)** | Keep roadmap design: our gateway calls Ollama's HTTP API directly; LiteLLM not introduced without a documented impact assessment and owner approval | 2026-11-19 | ADR-0003; `docs/architecture/HARNESS_ASSESSMENT.md` §6; roadmap §20 row **to be added** on next roadmap revision (pending approval) |
| D-36 | Skills Registry (new) | **Ruled by owner direction (AP-003)** | Versioned skills inside prompt/skill management (HC-16): interface in C, implementation in F. Skills grant no permissions | 2026-11-19 | ADR-0003; assessment §6; roadmap §5.1/§12 amendment **proposed, not applied** (DV-003) |
| D-37 | Per-operation model overrides + optional `review` slot | **Ruled (AP-005)**: include in architecture and implementation plan (C: operation IDs in `AgentSpec`; F: router precedence operation > agent > slot > global) | Adopted for planning; roadmap amendment still pending with D-35/D-36 (DV-003) | 2026-11-19 | `docs/architecture/MODEL_ROUTING.md`; roadmap §5.1, §13 C/F, §20; Plan GW-04 |

---

## 5. Chronological Change History

**Entry format.** One row per file or component change. The Change ID (CH-nnnn) is referenced by the File Change Register and the completion reports.

| Change ID | Date/Time (IST) | Part | File/Component | Change | Reason | Tests/Validation | Status |
|---|---|---|---|---|---|---|---|
| CH-0000 | 2026-11-18 15:16 | Pre-A (planning) | `docs/` (directory) | Created the directory (it did not exist) | Required location for the three authorised documents | Existence verified before creation (`Test-Path` returned False) | Done |
| CH-0001 | 2026-11-18 15:16 | Pre-A (planning) | `docs/IMPLEMENTATION_ROADMAP.md` | Created (v2.0): master roadmap, 18 parts / 6 milestones, 10 agents, HC-01…HC-21 mapping, decisions D-01…D-34 | Owner instruction #2 §6 File 1; supersedes the draft v1.0 roadmap | Manual consistency review (§8, VR-001) | Done, awaiting owner review |
| CH-0002 | 2026-11-18 15:16 | Pre-A (planning) | `docs/DETAILED_WORKING_PLAN.md` | Created (v1.0): methodology, session protocol, harness/agent/LangGraph/gateway/tool/sandbox sequences, per-part checklists A–R, templates | Owner instruction #2 §6 File 3 | Manual consistency review (VR-001) | Done, awaiting owner review |
| CH-0003 | 2026-11-18 15:16 | Pre-A (planning) | `docs/IMPLEMENTATION_TRACKER.md` | Created (this file) | Owner instruction #2 §6 File 2 | Manual consistency review (VR-001) | Done, awaiting owner review |
| CH-0004 | 2026-11-19 04:55 | A | `docs/parts/PART-A-PLAN.md` | Created Part A plan (scope, decisions, dependencies, AC→test map, risks) | Plan §20 Part A [ARCH]; AP-003 | Review at G-A (DV-001) | Done |
| CH-0005 | 2026-11-19 05:00 | A | Root repository files | Created `pyproject.toml` (uv workspace root, ruff/mypy/pytest/coverage/import-linter config), `.python-version`, `.gitignore`, `.gitattributes`, `.dockerignore`, `.env.example` | Roadmap §8, §13 A; D-26 (a) | TR-0012, TR-0015; VR-002, VR-003 | Done |
| CH-0006 | 2026-11-19 05:00 | A | `config/` | Created `defaults.yaml` and `profiles/{low_memory,standard,test}.yaml` | Roadmap §7, §13 A (layered config, profiles) | TR-0001 (`test_config.py`) | Done |
| CH-0007 | 2026-11-19 05:05 | A | `packages/core` (`devteam_core`) | Created package: `config/` (layered loader, strict typed settings, loopback-only Ollama URL), `logging/` (structlog JSON, correlation fields, redaction: secret registry + sensitive keys + token patterns), `tracing.py` (W3C trace context), `ids.py`, `clock.py`, `errors.py`, `paths.py`, `version.py` | HS-01 (HC-10, HC-11, HC-17 redaction); roadmap §13 A | TR-0001–TR-0004, TR-0013, TR-0015 | Done |
| CH-0008 | 2026-11-19 05:10 | A | `packages/state` (`devteam_state`) | Created package: SQLite engine (WAL, FK, busy timeout), declarative base, Alembic env + baseline `0001_baseline`, programmatic migrate helpers, online backup, startup `ensure_state_schema` (back up, then migrate) | Roadmap §13 A; Plan §11; RV-14 | TR-0004, TR-0012, TR-0015 | Done |
| CH-0009 | 2026-11-19 05:12 | A | `apps/api` (`devteam_api`) | Created package: app factory with lifespan, `/health`, `/version`, request-ID and trace middleware, `__main__` (loopback uvicorn), OpenAPI exporter; `apps/api/openapi.json` snapshot | Roadmap §13 A; HC-10/HC-11 | TR-0004, TR-0012, TR-0014, TR-0015 | Done |
| CH-0010 | 2026-11-19 05:25 | A | `apps/web` | Created React + TS + Vite shell: routing, layout, system status page, not-found page, typed client over generated `schema.d.ts`, Vitest tests, ESLint config, `package.json` + `package-lock.json` (TypeScript pinned to ~5.9.3, KI-009) | Roadmap §13 A | TR-0010; VR-007 | Done |
| CH-0011 | 2026-11-19 05:20 | A | `scripts/` | Created `doctor.py`, `check_agent_count.py` (L3 skeleton), `backup_state.py`, `export_openapi.py`, `benchmark_models.py`, `setup.ps1`, `dev.ps1` | Roadmap §13 A | TR-0006, TR-0008, TR-0009, TR-0014; VR-006 | Done |
| CH-0012 | 2026-11-19 05:30 | A | Tests | Created `packages/core/tests/*`, `packages/state/tests/*`, `apps/api/tests/*`, `tests/conftest.py`, `tests/integration/{test_migrations,test_api_boot}.py`, `tests/contract/test_openapi_snapshot.py`, `tests/scripts/{test_scripts,test_benchmark_scoring}.py`, package markers `tests/__init__.py`, `tests/scripts/__init__.py` | Roadmap §13 A tests | TR-0012, TR-0015 | Done |
| CH-0013 | 2026-11-19 05:35 | A | `CLAUDE.md`, `README.md`, `CHANGELOG.md` | Created engineering instructions (all roadmap §8 conventions, incl. tracker rule and session protocol), README (current state, install, config, commands, troubleshooting), changelog (Unreleased: Part A) | Roadmap §8; AC-A-06 | VR-008 | Done |
| CH-0014 | 2026-11-19 05:40 | A | `docs/adr/0001…0007` | Created ADR-0001…0007 (0001–0004 Accepted as mandated; 0005–0007 Proposed pending D-26/D-29/D-07) | Roadmap §9.3 | Review at G-A | Done |
| CH-0015 | 2026-11-19 05:40 | A | `.pre-commit-config.yaml`, `.github/workflows/{ci,security}.yml` | Created local hooks and CI/security workflows | Roadmap §7, §8, §13 A | Commands run locally (VR-002…VR-005); **not run on GitHub** (DV-002) | Done |
| CH-0016 | 2026-11-19 05:45 | A | `docs/architecture/HARNESS_ASSESSMENT.md` | Saved the Agent & Harness Architecture Assessment, updated with D-35/D-36 owner directions; marked non-authoritative | Owner instruction 2026-11-19 §1 | Review | Done |
| CH-0017 | 2026-11-19 05:15 | A | `uv.lock`, `apps/web/package-lock.json` | Generated by `uv lock` and `npm install` (never hand-edited) | AC-A-05; roadmap §8 | VR-003 | Done |
| CH-0018 | 2026-11-19 05:55 | A | `scripts/benchmark_models.py` (`_Requirement`, `_Rule`, `StructuredAnswer`, `CallResult.excerpt`, `_call`) | ID patterns moved into the JSON schema sent to the model; response excerpt recorded | DV-005 (first run invalid) | TR-0009, TR-0016 | Done |
| CH-0019 | 2026-11-19 06:00 | A | `docs/benchmarks/2026-11-19-run1-INVALID.{json,md}` | First benchmark run output preserved with an INVALID header | Evidence retention (DV-005) | TR-0011 | Done |
| CH-0020 | 2026-11-19 10:50 | A | `.gitleaks.toml`; `packages/core/tests/test_logging.py` (`test_token_patterns_are_redacted` parameters) | Added gitleaks config (default rules + allowlist of gitignored generated dirs only); inline `gitleaks:allow` on two synthetic redaction fixtures | AC-A-07 triage (VR-004) | VR-004 (planted-token negative control detected) | Done |
| CH-0021 | 2026-11-19 10:55 | A | `packages/state/src/devteam_state/migrate.py` (`current_revision`, `VERSION_TABLE`) | Read `alembic_version` directly instead of via `MigrationContext` (removed two INFO log lines per `/health` call seen in the smoke test) | Log hygiene (HC-10) | TR-0015 | Done |
| CH-0022 | 2026-11-19 11:00 | A | `docs/IMPLEMENTATION_TRACKER.md` (§1–§4, §5–§11) | Recorded AP-003, decisions D-07/D-20/D-21/D-26/D-33/D-34/D-35/D-36, deviations DV-001…DV-005, Part A changes, tests and validations | Tracker rule | — | Done |
| CH-0023 | 2026-11-19 11:00 | A | `docs/benchmarks/2026-11-19-baseline.{json,md}`, `docs/parts/PART-A-COMPLETION-REPORT.md` | Valid benchmark report (generated by the script, run 2); Part A completion report | AC-A-03; Plan §3.4 | TR-0016, TR-0017 | Done |
| CH-0024 | 2026-11-19 11:02 | A | **Correction** of timestamps in this tracker | The times written for AP-003, CH-0004…CH-0019, TR-0001…TR-0009 and VR-003/VR-006/VR-009 (all values before 10:00) were taken from a shell that returned **UTC** while labelled IST. **Add 5 h 30 min** to read them as IST (session started ≈10:24 IST). TR-0011's window "05:15–10:40" is wrong: the actual run 1 window is **10:37:55–10:44:59 IST** (from the report's `started_utc`/`finished_utc`). Rows were not rewritten, per tracker rule 9 | Tracker accuracy | — | Done |
| CH-0025 | 2026-11-19 12:46 | Pre-B (docs) | `docs/architecture/MODEL_ROUTING.md` | Created model-routing audit (implemented vs planned) and proposed design (precedence, validation rules, draft `models.yaml`, hardware rules, D-37 proposal) | AP-004 | Manual review against code, config and `ollama list` | Done (proposal) |
| CH-0026 | 2026-11-19 12:48 | B (planning) | `docs/parts/PART-B-PLAN.md` | Created Part B plan per Plan §21.1 template, incl. simple-words summary, decisions (D-11/15/22/29/31), data model, design, files, dependencies, AC→test map, demo, risks | Plan §20 Part B [ARCH]; AP-004 | Owner confirmation pending | Done (awaiting confirmation) |
| CH-0027 | 2026-11-19 12:50 | Cross-part | `docs/PROJECT_PROGRESS_TRACKER.md` | Created the permanent simple-language progress tracker (summary, A–R roadmap, decisions, approvals, per-part 10-field detail, routing status, corrections, glossary) | AP-004 | Manual consistency check against this tracker | Done |
| CH-0028 | 2026-11-19 12:52 | Cross-part | `CLAUDE.md` §2 | Added the rule to update `docs/PROJECT_PROGRESS_TRACKER.md` at the end of every part and before every approval request | AP-004 (owner: keep it updated throughout A–R) | — | Done |
| CH-0029 | 2026-11-19 12:52 | Cross-part | `docs/IMPLEMENTATION_TRACKER.md` (§1, §2, §3, §4, §5, §6, §11, §13) | Recorded AP-004, D-37 proposal, D-22 discrepancy, D-31 proposal, Part B "Planning", new changes; tracker rule 10 | Tracker rule | — | Done |
| CH-0030 | 2026-11-19 13:05 | Cross-part | `docs/PROJECT_PROGRESS_TRACKER.md` (whole file); `CLAUDE.md` §2 (progress-tracker paragraph) | Replaced the detailed simple-language tracker with a one-paragraph-per-run Development Run History (Runs 1–8); CLAUDE.md rule updated to match | Owner instruction 2026-11-19 (simplify tracker) | — | Done |

**Activity outside the project (recorded for transparency; not repository changes):**
- Earlier in the planning session, the specification's text was extracted to a temporary file in the coding agent's session scratchpad (outside the workspace) for reading.
- The draft roadmap v1.0 was also written there.
- Session-memory notes were saved in the coding agent's private memory directory (outside the workspace).
- None of these are part of the project.

**Activity on 2026-11-19 outside versioned files (Part A, under AP-003):**

| Activity | Detail |
|---|---|
| Installed system tool | Gitleaks 8.30.1 via `winget install --id Gitleaks.Gitleaks --scope user` |
| Pulled Ollama models | `qwen2.5:3b`, `qwen2.5-coder:3b` (benchmark candidates, Spec §9.2). Pre-existing local models were not touched |
| Created local Python environment | `.venv/` via `uv sync` |
| Installed web dependencies | `apps/web/node_modules/` via `npm install` |
| Created local runtime data (gitignored) | `data/state.db` and `data/logs/` by the dev smoke test |
| Wrote session scratch files (outside the workspace) | benchmark smoke output, logs, gitleaks report |
| Created empty directories | `docs/runbooks/`, `docs/evals/` for later parts (Git does not track empty directories) |
| Not done | No machine environment variables changed. No Git commit, push or GitHub operation |

---

## 6. File Change Register

| Change ID | Action | Path | Exact location of change | What changed | Related requirement / AC |
|---|---|---|---|---|---|
| CH-0000 | Created | `docs/` | Workspace root | New directory | Owner instruction #2 §9 (minimum directory allowed) |
| CH-0001 | Created | `docs/IMPLEMENTATION_ROADMAP.md` | Whole file (§1–§21) | New document | Owner instruction #2 §6 File 1; Spec §27 |
| CH-0002 | Created | `docs/DETAILED_WORKING_PLAN.md` | Whole file (§1–§21) | New document | Owner instruction #2 §6 File 3 |
| CH-0003 | Created | `docs/IMPLEMENTATION_TRACKER.md` | Whole file (§1–§14) | New document | Owner instruction #2 §6 File 2 |

| CH-0004 | Created | `docs/parts/PART-A-PLAN.md` | Whole file | Part A plan | Plan §20 A; AP-003 |
| CH-0005 | Created | `pyproject.toml`, `.python-version`, `.gitignore`, `.gitattributes`, `.dockerignore`, `.env.example` | Whole files | Workspace root and repository conventions | Roadmap §8; D-26 |
| CH-0006 | Created | `config/defaults.yaml`, `config/profiles/low_memory.yaml`, `standard.yaml`, `test.yaml` | Whole files | Layered configuration and profiles | Roadmap §13 A |
| CH-0007 | Created | `packages/core/pyproject.toml`; `packages/core/src/devteam_core/{__init__,version,errors,clock,ids,paths,tracing}.py`, `config/{__init__,settings,loader}.py`, `logging/{__init__,redaction,setup}.py`, `py.typed` | Whole files | Shared foundation | HS-01; HC-10, HC-11, HC-17; NFR-005, NFR-007 |
| CH-0008 | Created | `packages/state/{pyproject.toml,alembic.ini}`; `src/devteam_state/{__init__,engine,migrate,backup,schema}.py`, `models/__init__.py`, `migrations/{env.py,script.py.mako,versions/0001_baseline.py}`, `py.typed` | Whole files | Platform state foundation | Roadmap §13 A; Plan §11 |
| CH-0009 | Created | `apps/api/pyproject.toml`, `apps/api/openapi.json`; `src/devteam_api/{__init__,__main__,main,openapi_export}.py`, `routers/{__init__,system}.py`, `middleware/{__init__,request_context}.py`, `py.typed` | Whole files | Platform API `/health`, `/version` | Roadmap §13 A |
| CH-0010 | Created | `apps/web/{package.json,package-lock.json,index.html,vite.config.ts,tsconfig.json,eslint.config.js}`; `src/{main.tsx,App.tsx,App.test.tsx,styles.css}`, `src/api/{client.ts,schema.d.ts}`, `src/components/Layout.tsx`, `src/pages/{SystemStatusPage,NotFoundPage}.tsx`, `src/test/setup.ts` | Whole files | UI shell + generated typed client | Roadmap §13 A |
| CH-0011 | Created | `scripts/{doctor,check_agent_count,backup_state,export_openapi,benchmark_models}.py`, `scripts/{setup,dev}.ps1` | Whole files | Environment, benchmark and maintenance tooling | Roadmap §13 A; AC-A-03, AC-A-04; §4.4 L3 |
| CH-0012 | Created | `packages/core/tests/{test_config,test_logging,test_tracing_ids_clock_paths}.py`, `packages/state/tests/test_engine_backup.py`, `apps/api/tests/test_system.py`, `tests/{__init__,conftest}.py`, `tests/integration/{test_migrations,test_api_boot}.py`, `tests/contract/test_openapi_snapshot.py`, `tests/scripts/{__init__,test_scripts,test_benchmark_scoring}.py` | Whole files | Unit, integration, contract and script tests | Roadmap §13 A tests |
| CH-0013 | Created | `CLAUDE.md`, `README.md`, `CHANGELOG.md` | Whole files | Repository documentation | Roadmap §8; AC-A-06 |
| CH-0014 | Created | `docs/adr/0001-langgraph-orchestration.md` … `0007-docker-distribution.md` | Whole files | ADRs | Roadmap §9.3 |
| CH-0015 | Created | `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.github/workflows/security.yml` | Whole files | Local and CI gates | Roadmap §7, §8 |
| CH-0016 | Created | `docs/architecture/HARNESS_ASSESSMENT.md` | Whole file | Assessment | Owner instruction 2026-11-19 |
| CH-0017 | Created | `uv.lock`, `apps/web/package-lock.json` | Whole files (generated) | Lockfiles | AC-A-05 |
| CH-0018 | Modified | `scripts/benchmark_models.py` | `_Requirement`, `_Rule`, `StructuredAnswer`, `CallResult.excerpt`, `_call()` | Pattern in schema; excerpt recorded | DV-005 |
| DV-006 | 2026-11-19 | B | No project-delete endpoint in Part B (roadmap scope says "projects CRUD") | Deletion is irreversible and the Approval Service (HC-13) arrives in C; D-31 still open | Owner (AP-005) | Roadmap §13 B |
| CH-0019 | Created | `docs/benchmarks/2026-11-19-run1-INVALID.json`, `.md` | Whole files (renamed from the run's default output name; INVALID header prepended to `.md`) | Invalid-run evidence | DV-005 |
| CH-0020 | Created / Modified | `.gitleaks.toml` (created); `packages/core/tests/test_logging.py` lines 141–143 (modified) | Whole file; two parametrize entries + comment | Narrow secret-scan allowlist | AC-A-07 |
| CH-0021 | Modified | `packages/state/src/devteam_state/migrate.py` | `current_revision()`, new `VERSION_TABLE`; removed `MigrationContext` import | Silent revision read | HC-10 |
| CH-0022 | Modified | `docs/IMPLEMENTATION_TRACKER.md` | §1, §2, §3, §4, §5, §6, §7, §8, §9, §10, §11, §12 | Part A record | Tracker rule |
| CH-0023 | Created | `docs/benchmarks/2026-11-19-baseline.json`, `docs/benchmarks/2026-11-19-baseline.md`, `docs/parts/PART-A-COMPLETION-REPORT.md` | Whole files | Benchmark report (script output) and completion report | AC-A-03; Plan §21.2 |
| CH-0024 | Modified | `docs/IMPLEMENTATION_TRACKER.md` | §5 (new row only) | Timestamp correction note | Tracker rule 9 |
| CH-0025 | Created | `docs/architecture/MODEL_ROUTING.md` | Whole file | Routing audit + proposed design | HC-02…HC-05; D-06; D-37 (proposed) |
| CH-0026 | Created | `docs/parts/PART-B-PLAN.md` | Whole file | Part B plan | Roadmap §13 B; AC-B-01…05 |
| CH-0027 | Created | `docs/PROJECT_PROGRESS_TRACKER.md` | Whole file | Simple-language tracker | AP-004 |
| CH-0028 | Modified | `CLAUDE.md` | §2 Tracker-update rule (one new bullet) | Progress-tracker rule | AP-004 |
| CH-0029 | Modified | `docs/IMPLEMENTATION_TRACKER.md` | §1, §2 (Part B row), §3 (AP-004), §4 (D-06, D-22, D-31, D-37), §5, §6, §11, §13 (rule 10) | Status record | Tracker rule |
| CH-0030 | Modified | `docs/PROJECT_PROGRESS_TRACKER.md`; `CLAUDE.md` | Whole file; §2 last paragraph | Run-history format | Owner instruction |

| Category | Files |
|---|---|
| Files modified | `docs/IMPLEMENTATION_TRACKER.md` (CH-0022); `scripts/benchmark_models.py` (CH-0018); `packages/core/tests/test_logging.py` (CH-0020); `packages/state/src/devteam_state/migrate.py` (CH-0021) |
| Files deleted | `docs/benchmarks/2026-11-19-baseline.{json,md}` from run 1: renamed to `*-run1-INVALID.*` (CH-0019), not lost |
| Pre-existing files, untouched | `DevTeam_AI_Master_Development_Specification_v2.docx`, `docs/IMPLEMENTATION_ROADMAP.md`, `docs/DETAILED_WORKING_PLAN.md`, `.git/` |

---

## 7. Test Execution Log

| Test Run ID | Date/Time | Part | Command | Passed | Failed | Skipped (reason) | Evidence location |
|---|---|---|---|---|---|---|---|
| TR-0001 | 2026-11-19 05:01 | A | `uv run pytest packages/core -q` | 72 | 0 | 0 | Console (session) |
| TR-0002 | 2026-11-19 05:02 | A | `uv run pytest packages/core -q -k canary` (canary + new negative control) | 2 | 0 | 71 deselected (filter) | Console |
| TR-0003 | 2026-11-19 05:03 | A | `uv run pytest -q` (all Python suites) | 94 | **1** | 0 | `test_unsafe_request_ids_are_replaced[é-unicode]`: **test bug**, the HTTP client cannot encode non-ASCII `str` headers. Fixed by sending raw bytes (product code unchanged) |
| TR-0004 | 2026-11-19 05:04 | A | `uv run pytest -q` | 95 | 0 | 0 | Console |
| TR-0005 | 2026-11-19 05:06 | A | `uv run ruff check .` / `ruff format --check .` / `mypy packages apps` / `lint-imports` | ruff: 11 findings → fixed; mypy: 2 test typing errors → fixed; import-linter 2/2 kept | — | — | Final clean state in VR-002 |
| TR-0006 | 2026-11-19 05:12 | A | `uv run pytest tests/scripts -q` | 16 | 0 | 0 | Console |
| TR-0007 | 2026-11-19 05:14 | A | `scripts/benchmark_models.py --models qwen2.5:0.5b --runs 1` (pipeline smoke, scratchpad output) | completed (structured 0/1, code 1/1) | — | — | Session scratchpad (not in repo) |
| TR-0008 | 2026-11-19 05:20 | A | `uv run pytest tests/scripts tests/contract -q` | 33 | 0 | 0 | Console |
| TR-0009 | 2026-11-19 06:00 | A | `uv run pytest tests/scripts/test_benchmark_scoring.py -q` (after CH-0018) | 16 | 0 | 0 | Console |
| TR-0010 | 2026-11-19 10:47 | A | `npm run gen:api`, `check:api`, `lint`, `typecheck`, `test`, `build` (apps/web) | Vitest 5/5; lint, typecheck and build clean | 0 | 0 | Console |
| TR-0011 | 2026-11-19 05:15–10:40 | A | `uv run python scripts/benchmark_models.py --runs 3` (run 1) | Completed, **INVALID** (structured task design flaw, DV-005) | — | — | `docs/benchmarks/2026-11-19-run1-INVALID.*` |
| TR-0012 | 2026-11-19 10:48 | A | `uv run pytest -m "not llm and not docker and not slow and not acceptance" --cov --cov-report=term` | 128 | 0 | 0 | Coverage **95%** total (branch); console |
| TR-0013 | 2026-11-19 10:51 | A | `uv run pytest packages/core/tests/test_logging.py -q` (after CH-0020) | 20 | 0 | 0 | Console |
| TR-0014 | 2026-11-19 10:52 | A | `./scripts/dev.ps1 -Smoke` (real API + Vite; `/health` direct and via proxy) | `SMOKE OK` | 0 | — | Console; no listeners left on 8000/5173 |
| TR-0015 | 2026-11-19 10:51 | A | `uv run pytest -m "not llm and not docker and not slow and not acceptance" -q` (after CH-0021) | 128 | 0 | 0 | Console |
| TR-0016 | 2026-11-19 10:51–10:58 | A | `uv run python scripts/benchmark_models.py --runs 3` (run 2, corrected task; machine otherwise idle) | 4/4 models: structured 3/3, code 3/3 | 0 | 7B not run (insufficient free RAM, Spec §9.3) | `docs/benchmarks/2026-11-19-baseline.{json,md}` |
| TR-0017 | 2026-11-19 10:59 | A | **Final regression:** `uv sync --locked`; ruff check + format; mypy strict; lint-imports; `check_agent_count.py`; `pytest -m "not llm and not docker and not slow and not acceptance" --cov`; web `check:api`, `lint`, `typecheck`, `test`, `build`; `gitleaks dir . --exit-code 1` | pytest **128**; Vitest **5**; all gates clean; coverage **95%**; gitleaks exit 0 | 0 | 0 | Console |

---

## 8. Validation Evidence Log

| Validation ID | Date/Time | Part | What was validated | Method | Result | Evidence |
|---|---|---|---|---|---|---|
| VR-001 | 2026-11-18 15:16 | Pre-A | Consistency of the three planning documents, as specified in owner instruction #2 §10 | Manual review by the coding agent after writing all three files. **Not an automated test** | See the final session report to the owner | This tracker §1–§6 and the session report |
| VR-002 | 2026-11-19 10:56 | A | Static gates | `uv run ruff check .`; `ruff format --check .`; `mypy packages apps scripts` (strict + pydantic plugin); `lint-imports`; `scripts/check_agent_count.py` | **Pass**: 0 ruff findings, 63 files formatted, mypy 38 files no issues, 2/2 import contracts kept, agent check passed (skeleton mode) | Console |
| VR-003 | 2026-11-19 10:48 | A | AC-A-05 reproducible lock | `uv lock` (fresh) then `uv sync --locked` | **Pass** ("Checked 60 packages") | Console |
| VR-004 | 2026-11-19 10:50 | A | AC-A-07 no secrets | `gitleaks dir . --redact --exit-code 1`. First scan: 9 findings, all triaged (5 in gitignored caches and `.venv` third-party code; 4 = deliberately synthetic redaction fixtures and their `.pyc`). Narrow config added (CH-0020); rescan: **no leaks, exit 0**. Negative control: a planted random `ghp_` token in a temp dir **was detected (exit 1)** | **Pass** | Console; report in session scratchpad |
| VR-005 | 2026-11-19 10:49 | A | Dependency audits (`security.yml` equivalents) | `npm --prefix apps/web audit --audit-level=high`; `uvx pip-audit --strict -r <uv export>` | **Pass**: npm 0 vulnerabilities; pip-audit "No known vulnerabilities found" | Console |
| VR-006 | 2026-11-19 05:19 | A | AC-A-04 Ollama local-only / single model | `uv run python scripts/doctor.py` | **Partial.** Loopback base URL OK; `OLLAMA_HOST` unset (binds 127.0.0.1) OK; server reachable (0.40.16). `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` **unset (WARN)**: owner action recommended (KI-004). Result 13 ok / 6 warn / 0 fail | Console |
| VR-007 | 2026-11-19 10:47 | A | OpenAPI drift gate has teeth | `openapi-typescript <drifted copy> --check` vs clean snapshot | **Pass**: drifted exit 1, clean exit 0 | Console |
| VR-008 | 2026-11-19 11:00 | A | AC-A-06 CLAUDE.md covers roadmap §8 list | Manual checklist: ten-agent restriction ✔, agent boundaries ✔, LangGraph conventions ✔, gateway-only ✔, harness/security ✔, tool/sandbox restrictions ✔, approvals ✔, coding/testing ✔, uv/npm rules ✔, adding/modifying agents ✔, no bypass of central infrastructure ✔, tracker rule ✔, session protocol ✔ | **Pass** (manual review, not automated) | `CLAUDE.md` §1–§11 |
| VR-009 | 2026-11-19 05:02 | A | Redaction canary has teeth | `test_negative_control_canary_leaks_without_redaction`: with the redaction processor removed, the canary **is** emitted | **Pass** | TR-0002 |

---

## 9. Known Issues

| ID | Date | Description | Impact | Owner of resolution | Status |
|---|---|---|---|---|---|
| KI-001 | 2026-11-18 | A hidden `.git` directory already exists in the workspace root. It was not created during planning, and its contents (commits, remotes) have not been inspected | Affects platform repository set-up in Part A | Owner ruling D-33 | **Resolved 2026-11-19**: inspected read-only, empty (no commits, no remotes). D-33 (a) applied, owner approval pending |
| KI-004 | 2026-11-19 | `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` are not set on the owner's machine (Spec §9.3) | AC-A-04 partial. The platform gateway (Part F) will still serialise calls and unload models itself | Owner (README has the 3 commands; changing user env vars was not done without consent) | Open |
| KI-005 | 2026-11-19 | Low free memory: ~2.0–2.3 GB available at session start; **minimum ~450 MB free** while a 3B model was loaded (benchmark run 1) | Risk R2 realised: 3B models run under heavy memory pressure; 7B not benchmarked | Owner (close apps) / design (one model at a time, unload before sandbox) | Open |
| KI-006 | 2026-11-19 | CI workflows never ran on GitHub (no push authorised) | AC-A-02 unverifiable (DV-002) | Owner authorisation of Git/GitHub | Open |
| KI-007 | 2026-11-19 | Docker Desktop installed but engine not running; distribution/licensing undecided | Blocks Part E only | Owner ruling D-07 | Open |
| KI-008 | 2026-11-19 | Starlette emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` in API tests | None today; may break on a future Starlette upgrade | Revisit on next dependency update | Open (monitor) |
| KI-009 | 2026-11-19 | TypeScript pinned to `~5.9.3`: TS 7.0.2 is current, but `openapi-typescript@7.13.0` requires `^5.x` and `typescript-eslint@8.73.0` requires `<6.1.0` | Web toolchain one major behind | Revisit when those packages support TS 6/7 | Open (accepted) |
| KI-010 | 2026-11-19 | Gitleaks is on `PATH` only in shells started after the winget install (`doctor` in an old shell shows it as missing) | Cosmetic | — | Open (documented in README troubleshooting) |
| KI-011 | 2026-11-19 | Embedding model not benchmarked in Part A (roadmap §9.1: "chosen by benchmark (A/F)") | None until RAG (Part F) | Part F plan | Deferred to F |
| KI-002 | 2026-11-18 | Any earlier AI Agent Harness discussion outside this session was not accessible. The roadmap uses only the 21-item harness list from owner instruction #2 | Possible gap if earlier discussion had additional requirements | Owner to confirm nothing is missing | Open |
| KI-003 | 2026-11-18 | Repository template files were intentionally not provided. Structure and conventions were derived from the stated conventions only | None expected | — | Accepted by design |

---

## 10. Deviations from the Roadmap

| ID | Date | Part | Deviation | Reason | Approved by | Affected sections |
|---|---|---|---|---|---|---|
| DV-001 | 2026-11-19 | A | `docs/parts/PART-A-PLAN.md` was not confirmed by the owner before implementation (Working Plan §20 asks for confirmation) | AP-003 instructs not to wait on routine decisions; the plan is presented for retrospective review at G-A | Pending (G-A) | Plan §20 Part A [ARCH] |
| DV-002 | 2026-11-19 | A | Working Plan §16.1 steps 2–4 and 7 not performed: no GitHub repository, branch protection, part branch, PR or CI run | Git commits and GitHub pushes are explicitly excluded by AP-003. CI workflow files exist; their commands were run locally instead. AC-A-02 cannot be verified | Pending owner authorisation | Roadmap §13 A (AC-A-02), §18.1 |
| DV-003 | 2026-11-19 | A | D-35/D-36 owner directions are not yet reflected in the roadmap body (§5.1, §12, §20) | Roadmap edits need owner approval (roadmap §21); recorded here and in ADR-0003 instead | Pending | Roadmap §5.1, §12, §20 |
| DV-004 | 2026-11-19 | A | Loopback-only validation of `ollama.base_url` added at config load in Part A (roadmap schedules localhost enforcement in F, GW-03) | Early fail-fast for the local-only rule (Spec §9.1); additive, low risk; the gateway will still enforce it in F | Recorded (minor, additive) | Roadmap §13 F; Plan GW-03 |
| DV-005 | 2026-11-19 | A | Benchmark structured task revised after the first full run (TR-0011) | The first design validated an ID format that was not in the JSON schema sent to the model, so every 3B model failed on format alone. The pattern is now part of the schema (measures schema reliability per Spec §22 Phase 0). The invalid run is kept as evidence | Recorded | Roadmap §13 A (AC-A-03) |

---

## 11. Pending Work & Next Authorised Action

**Pending:**

1. Part B implementation (in progress).
2. Owner approval of the model selection (owner: "keep pending; will approve later").
3. Owner rulings when they become necessary (never assumed): D-07 (before Part E), D-21, D-26/D-33 confirmation, D-31 final ruling, D-34 (commit the spec .docx; until ruled, the .docx is **not** committed).
4. Optional: Ollama env vars (KI-004); GitHub push/repository creation (not authorised; AC-A-01 fresh-clone can now be checked locally, AC-A-02 CI still needs GitHub).
5. Roadmap v2.1 amendment for D-35/D-36/D-37 (DV-003), on owner approval.

**Next authorised action:** Implement Part B per `docs/parts/PART-B-PLAN.md` (AP-005). Then completion report and **stop for G-B**.

---

## 12. Milestone Log

| Milestone | Parts | Status | Completed on | Report |
|---|---|---|---|---|
| M0 Foundation | A, B | In progress (A approved; B in progress) | — | `docs/parts/PART-A-COMPLETION-REPORT.md` |
| M1 Harness & Orchestration | C, D, E, F | Not started | — | — |
| M2 Discovery | G, H, I | Not started | — | — |
| M3 Build | J, K, L | Not started | — | — |
| M4 Verify & Deliver | M, N, O, P | Not started | — | — |
| M5 Evolve & Harden | Q, R | Not started | — | — |

---

## 13. How to Update This Tracker (rules for every future session)

1. **Session start:** read §1 and §11. Confirm the requested work matches the next authorised action.
2. **For every file created, modified or deleted:**
   - add a row to §5 (Change History) with a new `CH-nnnn`;
   - add a matching row to §6 (File Change Register) with the **exact location**: path plus section, function/class name or line range;
   - give the reason and the related requirement or AC.
3. **For every test run:** add a row to §7 with the exact command and the actual counts. Failed runs are recorded too.
4. **For every validation or demonstration:** add a row to §8.
5. **Decisions:** update §4 when the owner rules. Record the date and list the affected sections in all three documents.
6. **Deviations:** any departure from the roadmap or plan goes in §10 with the approval reference.
7. **Approvals:** only explicit owner approvals go in §3.
8. **Session end:** refresh §1 (status summary), §2 (part board), §9 (known issues) and §11 (pending work and next authorised action).
9. Never delete history rows. Correct mistakes with a new row that references the corrected `CH-`/`TR-`/`VR-` ID.
10. After **every** development run, append one plain-English paragraph to `docs/PROJECT_PROGRESS_TRACKER.md` (Development Run History). No technical detail there.

---

## 14. Entry Templates

**Change history row (§5):**

```text
| CH-nnnn | YYYY-MM-DD HH:MM | <Part> | <path or component> | <what changed> | <why> | <TR-/VR- refs or "not yet tested"> | <Done / In progress / Reverted> |
```

**File change register row (§6):**

```text
| CH-nnnn | Created/Modified/Deleted | <path> | <section / symbol / lines> | <summary> | <FR-/NFR-/AC-/D- refs> |
```

**Test execution row (§7):**

```text
| TR-nnnn | YYYY-MM-DD HH:MM | <Part> | `<exact command>` | <n> | <n> | <n (reason)> | <path to log/report> |
```

**Validation row (§8):**

```text
| VR-nnnn | YYYY-MM-DD HH:MM | <Part> | <AC or check> | <method> | <Pass/Fail> | <evidence ref> |
```

**Approval row (§3):**

```text
| AP-nnn | YYYY-MM-DD HH:MM | <Gate G-X / decision / plan> | Owner | <message or PR ref> | <conditions> |
```
