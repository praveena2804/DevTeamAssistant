# CLAUDE.md — Engineering Instructions for Coding Agents

These rules bind every coding agent (and human) working on the **DevTeam AI platform**.

**Authority order:**
1. Master Specification v2.0 (`DevTeam_AI_Master_Development_Specification_v2.docx`).
2. `docs/IMPLEMENTATION_ROADMAP.md` (**what** and **when**).
3. `docs/DETAILED_WORKING_PLAN.md` (**how**).
4. `docs/IMPLEMENTATION_TRACKER.md` (**what actually happened**).

If this file conflicts with them, they win. Report the conflict; do not silently resolve it.

---

## 1. Session protocol

**Start of every session:**
1. Read this file, `docs/IMPLEMENTATION_TRACKER.md` §1 (current status) and §11 (next authorised action).
2. Confirm the requested work matches the next authorised action. If it does not, stop and ask the owner.
3. Read the part's section in the roadmap (§13) and its checklist in the Working Plan (§20).
4. Check repository state: branch, working tree, last tag.
5. Run the fast suite to confirm a green baseline (§9 below). Record the run in the tracker.

**End of every session:**
1. Run the suites relevant to the part.
2. Update the tracker:
   - §1 status;
   - §2 board;
   - §5 and §6 changes, with **exact locations**;
   - §7 test runs, with exact commands and counts;
   - §9 issues;
   - §11 next action.
3. Never mark a part complete without its full gate procedure (roadmap §17).

**Gated delivery:**
- One part at a time.
- Each part ends with `docs/parts/PART-X-COMPLETION-REPORT.md`, then **stop and wait for explicit owner approval** of gate G-X.
- Approvals are never inferred or invented. Only explicit owner approvals go in tracker §3.

## 2. Tracker-update rule (mandatory)

Every file created, modified or deleted is recorded in `docs/IMPLEMENTATION_TRACKER.md` **in the same session**:
- §5 Change History and §6 File Change Register (path + section/symbol/lines, reason, requirement).

Tests are recorded only when actually executed, with:
- the command;
- passed, failed and skipped counts;
- the reason for every skip.

Planned work is never recorded as done. Mistakes are corrected with a new row, never by deleting history.

**Git and versioning (AP-005):**
- Local commits are authorised for completed work. **No push, publish or deploy** without separate owner approval.
- Branches: `main`; one branch per part, `part/<letter>-<slug>`, merged locally after gate approval.
- Messages: Conventional Commits, at most two lines.
- Versions: SemVer pre-1.0, one minor bump per approved part (A = 0.1.0, B = 0.2.0 …); MVP = `1.0.0-mvp`. Tags `vX.Y.Z` + `part-<letter>-approved`.
- The spec `.docx` is not committed until D-34 is ruled.

`docs/PROJECT_PROGRESS_TRACKER.md` is the owner's **Development Run History**. At the end of **every** development run, append one short paragraph (`**Run N — Part X**`) in everyday English saying only what was actually done in that run. No plans, test reports, file lists, risks or decisions there. Record partial work honestly, and record "no development changes" when there were none. Never delete earlier entries. Technical detail stays in `IMPLEMENTATION_TRACKER.md`.

## 3. The two systems: never confuse them

| | DevTeam AI **platform** (this repository) | Generated **target application** |
|---|---|---|
| Stack | React+TS+Vite, FastAPI, LangGraph, Ollama, SQLite, ChromaDB, Docker sandbox | Restricted by the Capability Catalog (MVP: React/HTML + FastAPI + SQLAlchemy/Alembic + SQLite) |
| Location | Repository source | `workspaces/<project_id>/generated/` (never inside platform source) |

## 4. Exactly ten agents (ADR-0002)

**The ten agents:**

| # | Agent | Package |
|---|---|---|
| 1 | BRD Analyst | `brd_analyst` |
| 2 | Requirements Finalizer | `requirements_finalizer` |
| 3 | Head Agent | `head` |
| 4 | Architecture Agent | `architect` |
| 5 | Database Developer | `database_developer` |
| 6 | Backend Developer | `backend_developer` |
| 7 | Frontend Developer | `frontend_developer` |
| 8 | Testing & Validation Agent | `tester` |
| 9 | Debugger & Code Reviewer | `debugger_reviewer` |
| 10 | Documentation & Reporting Agent | `documenter` |

**Hard rules:**
- **Adding an eleventh agent is prohibited.**
- Any change to `AgentRole`, `agents/` or the agent registry requires an ADR **and** owner approval.
- Enforcement has five layers (roadmap §4.4): closed enum, registry, `scripts/check_agent_count.py`, graph topology test, and this rule.

**Never register these as agents:**
- Deterministic services: Document Extractor, Feasibility Gate, Catalog validator, Requirements Policy Engine, Integration/Build Service, Test Runner, Migration Runner, Traceability, Completion Gate, Packager, Secret Scanner, GitHub Deployer, Change Impact Analyser, Spec Freeze.
- Harness components HC-01…HC-21.
- Head Agent internals.
- Graph gate, guard or router nodes.

**Boundaries:** each agent's responsibilities, allowed tools, write scope and prohibitions are defined in roadmap §4.1 and Spec §6. In summary:
- **Head Agent:** never writes target source, runs commands or tests, approves, or sets Verified. It runs inside the same harness as every other agent; **there is no privileged exception**.
- **Requirements Finalizer:** never decides criticality (code does) and never opens a second clarification round.
- **Database Developer:** never applies migrations. The harness generates and applies them in the sandbox.
- **Testing & Validation Agent:** never records test results. Only the harness runner does.
- **Debugger & Code Reviewer:** never edits `tests/`, except in a reviewer-approved test-maintenance task.
- **Documentation & Reporting Agent:** never invents endpoints, results, files or URLs.

## 5. Harness and security rules (ADR-0003)

**One door:**
- The harness (`packages/harness`, HC-01…HC-21) is the **only** door to models, files, commands, network, databases and GitHub.
- Agents receive an `AgentContext` that exposes controlled proxies only.

**Rules every agent obeys:**
- **LLM Gateway only.** Every model call (including embeddings, stubs and tests) goes through `LLMGateway`.
  - Only `llm_gateway/providers/` may import HTTP clients or model SDKs.
  - The gateway calls Ollama's HTTP API directly. **No LiteLLM or other proxy layer** (decision D-35, owner direction 2026-11-19).
  - Ollama must be loopback-only; configuration validation rejects anything else.
- **Agent imports.** Agent packages may import only `devteam_core` and `devteam_harness.agent_sdk`. Banned:
  - `open`, `os`, `pathlib` writes, `subprocess`, `shutil`, `socket`;
  - `httpx`, `requests`, `ollama`, `docker`;
  - any other agent.

  These bans are enforced by Ruff banned-API rules and import-linter (activated in Part C).
- **Deny by default.** Effective permission = AgentSpec ∩ central policy (`config/guardrails/`) ∩ task scope ∩ stage. A task can only narrow an agent's scope, never widen it.
- **Fail closed.** If a mandatory harness control is missing or disabled, the agent does not run.
- **No generic shell.** Command execution uses allowlisted recipes in the Docker sandbox:
  - `network=none`, resource limits, non-root, read-only rootfs.
  - **Generated code never runs on the host.** If Docker is unavailable, the work is **Blocked**, never executed unsafely.
- **Untrusted content.** BRD text, retrieved chunks, tool output (logs, test output) and agent-generated artefacts are *data*:
  - they are delimited and labelled with a trust level;
  - an agent's output never becomes instructions for another agent.
- **Prompts are not security controls.** Security is enforced in code, outside the model.
- **Secrets:**
  - stored only in the OS keyring (HC-17), referenced by `SecretReference`;
  - never placed in prompts, logs, audit, traces, packages or repositories;
  - logs are redacted by `devteam_core.logging` (registered values, sensitive keys, token patterns).
- **Honest status.** Agents cannot claim tests passed or set Verified. Only the Test Runner writes results; only the Completion Gate writes project status.
- **Skills (D-36).** Versioned skills live inside the prompt/skill registry (HC-16). A skill **grants no permission**; the harness remains the security boundary.

## 6. Approval requirements (HC-13)

- Gates: `feasibility_decision`, `clarification_form` (once), `project_approval`, `test_maintenance_approval`, `publish_approval`, `change_approval`, `db_switch_approval`.
- Approvals are enforced in code: graph topology, `build_entry_guard`, and a stage check on every build-stage tool call.
- No agent can approve anything.
- Nothing is published to GitHub without the separate public-visibility approval and a clean secret scan.

## 7. LangGraph conventions (ADR-0001)

- LangGraph is the only orchestrator.
- Graph shape: parent graph `project_run` with the subgraphs `discovery`, `build`, `delivery` and `change_request`.
- Thread ID = `run_id`; checkpoints are kept in `data/checkpoints.db`.
- Nodes return small state updates (**IDs only**). Domain facts live in SQLite (`data/state.db`).
- No node holds a graph reference.
- Routing functions are pure, return a closed `NodeName` enum and are unit-tested against the allowed-transition table.
- Gate nodes never write before `interrupt()`. Side-effecting nodes consult the attempt ledger (idempotency).
- Every node is wrapped by tracing and audit.
- LLM calls and sandbox runs are strictly sequential (one at a time, never overlapping; Spec §7.2).

## 8. Coding conventions

- **Python:** 3.12 (`.python-version`); typed with mypy strict; Ruff for lint and format (line length 110); Pydantic v2 at every boundary (HC-20).
- **Layout:** uv workspace (D-26):
  - `packages/core` (no internal dependencies);
  - `packages/state`;
  - `packages/harness`;
  - `packages/orchestration`;
  - `packages/services`;
  - `apps/api`, `apps/worker`, `apps/web`;
  - `agents/<ten packages>`.

  Dependency direction is in roadmap §7.1 and enforced by import-linter.
- **Configuration:**
  - Layers: `config/defaults.yaml` < `config/profiles/<profile>.yaml` < `.env` < environment (`DEVTEAM_<SECTION>__<KEY>`).
  - Validated at startup; unknown keys are rejected.
  - Never hardcode limits that belong in config.
- **Logging:** structured JSON via `devteam_core.logging`. Bind `project_id`, `run_id` and `task_id`. Never log secrets or prompt content.
- **IDs:** `devteam_core.ids.new_id(prefix)`.
- **Time:** `devteam_core.clock` (UTC, injectable).
- **Database:**
  - Alembic only, one revision per logical change, with upgrade **and** downgrade;
  - never edit an applied migration; never hand-edit the database;
  - every domain table carries `project_id`;
  - existing databases are backed up automatically before migration.
- **Frontend:** React + TS strict; API types are generated from `apps/api/openapi.json` (`npm run gen:api`). Never hand-write API types.
- **Smallest safe change:** no refactoring outside the authorised scope without owner approval.

## 9. Testing conventions

**Markers:**

| Marker | Meaning |
|---|---|
| none | unit |
| `integration` | cross-module tests |
| `contract` | schema and OpenAPI snapshots |
| `security` | security suite |
| `docker` | needs a running Docker engine |
| `llm` | needs local Ollama; **never run in CI** |
| `slow` | long end-to-end tests |
| `acceptance` | Spec §23 scenarios |

**Fast suite:**
```text
uv run pytest -m "not llm and not docker and not slow and not acceptance"
```

**Static gates:**
```text
uv run ruff check . ; uv run ruff format --check . ; uv run mypy packages apps scripts
uv run lint-imports ; uv run python scripts/check_agent_count.py
```

**Web:**
```text
npm --prefix apps/web run lint ; npm --prefix apps/web run typecheck ; npm --prefix apps/web test ; npm --prefix apps/web run build
```

**Rules:**
- Safety-critical modules (HC-01, 06, 13, 14, 15, 17, 21, Completion Gate, Policy Engine) are **test-first**.
- Every security test has a negative control proving it fails when the protection is removed.
- Never weaken, skip or delete a test to make a build pass. Every skip needs a recorded reason.
- Coverage targets: ≥ 90% for harness, contracts and the safety-critical services; ≥ 80% elsewhere.

## 10. Dependency rules

- Python dependencies are declared in the owning package's `pyproject.toml`. The lockfile is updated only with `uv lock`; CI uses `uv sync --locked`. Never hand-edit `uv.lock`.
- Frontend dependencies go through `npm install` in `apps/web`; `package-lock.json` is committed; CI uses `npm ci`.
- No paid services, APIs or subscriptions (NFR-013). No AGPL dependencies (PyMuPDF is excluded).
- Prefer the standard library and existing dependencies. Every new dependency is listed in the part plan with its reason.

## 11. Git and GitHub

- Branches: `part/<letter>-<slug>`, `fix/<slug>`.
- Commits follow Conventional Commits; PRs are one per part; tags are `part-<x>-approved`.
- **Commits and pushes only when the owner has explicitly authorised Git/GitHub use.** As of 2026-11-19 this is **not** authorised.
- Never force-push `main`. Never commit `.env`, `data/`, `workspaces/`, databases, logs or model files.
- Tokens are never pasted into chat or written to files.
