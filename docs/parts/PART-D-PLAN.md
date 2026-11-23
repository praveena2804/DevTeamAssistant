# Part D — LangGraph Orchestration, Ten Stub Agents & Head Agent Deterministic Core — Plan

Roadmap ref: §13 Part D, §4.3, §6 · Milestone: M1 · Gate: **G-D** · Working Plan §7 (LG-02…LG-11, LG-13), §20 Part D, HS-17, HS-18 · Spec §7, §20, §21, §22 Phase 2, §23 #7/#10/#23 · Date: 2026-11-23

| Field | Value |
|---|---|
| Status | **Approved** (AP-015, 2026-11-23). Checkpoint D.1 implemented; D.2 not started (waits for the D.1 review) |
| Preconditions | G-C approved (**done**, AP-013, v0.3.0). D-27 (b), D-38…D-42 approved; **D-43 deferred** (AP-015) |
| Harness components | Implements HC-07; HC-19 checkpointing; HC-12 resume and crash recovery; HC-13 interrupt mechanics; HC-10 progress, events and metrics; HC-11 graph spans and trace viewer; HC-21 run history. Integrates every Part C component |
| Agents | All 10 as **stubs**. Agent 3 (Head) also gets its **deterministic core**: plan validator, scheduler, dispatcher, failure router and template planner |
| Branch | `part/d-orchestration` (local), one local commit per checkpoint D.1–D.5. Push only with your separate approval |

---

## 0. Part D in simple words

Part C built the safety walls. Part D builds the **assembly line** that moves a project from start to finish. It also proves that the line can be paused, stopped and restarted without doing anything twice.

When it is done:

1. **You can press "Start" on a project and watch it run** through every stage in the browser:
   - reading the documents;
   - analysis;
   - feasibility;
   - questions;
   - your approval;
   - planning;
   - architecture, database, backend and frontend;
   - testing;
   - repair;
   - documentation;
   - packaging;
   - the publish question;
   - the final report.
2. **The ten agents are stand-ins ("stubs") that do not think yet.** Each one returns a correct-shaped answer from a prepared script ("fake AI"). It goes through the same safety walls a real agent will use. Every screen and report labels the run **"Stub run — no AI used, nothing verified"**.
3. **The run stops at each approval point and waits for you:** feasibility, project approval and publishing. You approve or cancel on a simple page. **Nothing is published anywhere.** The publish step is a stand-in that refuses to touch the internet.
4. **Building cannot start without your approval.** There is no route around it in the workflow. If anyone forces it, the system refuses and writes the attempt to the activity log.
5. **The Head Agent's planning rules are real code:**
   - it builds a task list from a fixed template;
   - it checks the list: no circular dependencies, no unknown agents, no missing steps, no writing outside allowed folders, and every requirement covered;
   - it rejects bad lists.
6. **The work runs in a separate background program (the "worker"), not inside the website.** If the worker crashes, or the laptop restarts, it picks up from the last saved point and **does not repeat finished steps**. You can also pause, resume or cancel.
7. **You can see what happened:** a live progress page, a run history and a "trace" view showing each step, how long it took and whether it succeeded. Document text and secrets never appear there.

---

## 1. Scope

**In scope:** roadmap §13 Part D and Working Plan §20 Part D, delivered as five checkpoints under one gate (the same pattern as D-32 for Part C).

| Checkpoint | Working Plan steps | What |
|---|---|---|
| **D.1 Foundations** | LG-01 completion, LG-05 (part), LG-06 (tables) | Add LangGraph to the project and check its licences and security. Add the missing contracts: `NodeName`, `NodeKind` and the allowed-transition table (LG-01 listed these for C, but they were not built; see §4). Migration `0005_orchestration`. Settings section `orchestration`. Import rules for the new packages. A guard that LangSmith tracing (cloud telemetry) is off |
| **D.2 Head core and stubs** | Head core; stubs | Head Agent deterministic core: validator, scheduler, dispatcher, failure router, template planner (pure functions, no I/O). Stub behaviour for all 10 agents. Fixture responder for the fake provider. Stub services: feasibility, spec freeze, integration/build, test runner, completion gate, packager, deployer |
| **D.3 Graph** | LG-02, LG-03, LG-04, LG-05, LG-06, LG-09, LG-10, LG-13 | Node wrappers (agent, service, gate, guard, router). Four subgraphs plus the parent graph. `build_entry_guard`. Gate nodes using `interrupt()`. SqliteSaver checkpointer on `data/checkpoints.db`. Attempt ledger and two-phase action helper. `Send` fan-out for deterministic nodes (D-27). Topology tests. Generated diagram in `docs/architecture/orchestration.md` |
| **D.4 Worker and run control** | LG-07, LG-08, LG-11, HS-17 | `apps/worker`: job table, single-worker lease, heartbeat, startup recovery. Run control API: start, pause, resume, cancel, gate decision. Kill-and-restart recovery test |
| **D.5 Progress, observability, UI** | HS-18, E2E-2 | Progress events delivered live over SSE. Metrics. Graph spans and the trace viewer. Run history. UI: Build Progress page, minimal gate pages, trace viewer, run history. OpenAPI and web types regenerated |

**Out of scope:**

| Excluded | Comes in |
|---|---|
| Any real model call; the Ollama adapter, routing, RAG and context manager | F |
| Docker sandbox and running generated code (integration/test runner stubs run **nothing**) | E |
| Real feasibility (Capability Catalog) | G |
| Real BRD analysis | H |
| Real clarification form, Project Understanding page, real Spec Freeze content | I (D creates only the minimal spec-version record the guard needs, D-39) |
| LLM planner and Architecture Agent behaviour | J |
| Real build agents | K, L |
| Real Test Runner results and evidence (the stub **never** records a test result) | M |
| Real repair loop limits and Completion Gate (the stub **never** sets a project verification status) | N |
| Real docs and ZIP package | O |
| Any GitHub access (the deployer stub has no network code) | P |
| Change-request behaviour: D builds only a compiled **skeleton subgraph**, and the API refuses to start a change run | Q |
| `test_maintenance_approval` and `db_switch_approval` gate nodes (the enum members already exist) | N, Q |

---

## 2. Decisions needed

### 2.1 Confirmation from the roadmap

| ID | Decision | Recommendation |
|---|---|---|
| **D-27** | Parallel execution | **(b) Confirm.** Only deterministic, model-free nodes may fan out (`Send`). The limit is `resources.max_parallel_deterministic` (already in config: 1 on `low_memory`, 2 on `standard`). LLM calls and sandbox runs are always one at a time. In D the only fan-out is extracting several documents in the intake node |

### 2.2 New decisions for Part D (proposed rulings)

| ID | Question | Options | Recommendation and reason |
|---|---|---|---|
| **D-38** | Where may the fake AI be used outside tests? Part C allowed it only in tests and the demo | (a) A run-level **stub mode**: `POST …/runs` takes `mode: "stub"`. The fake provider in fixture mode is selectable only for stub runs. Every stub-run record, event and UI view is labelled. Part F adds `mode: "real"`. (b) Test profile only (then there is no live demo and no worker kill demo on the real install) | **(a).** The gate demo needs a live run on your machine. The label, plus "the stub Completion Gate never writes a verification status" and "the stub Test Runner never writes a test result", stop a stub run from ever looking verified |
| **D-39** | The build-entry guard must re-read the approval **and the spec version** from SQLite (roadmap §6.5 layer 2). No spec-version table exists yet | (a) Add a minimal `spec_versions` table now (fields from the C.1 `SpecVersion` contract: id, project, run, version number, status draft/frozen, checksum, timestamps). Part I fills it with real content. (b) Keep the stub spec in memory | **(a).** The guard then checks real rows in D, so AC-D-03 is proven at runtime and not simulated. Part I extends the table instead of replacing it |
| **D-40** | An attempt that was interrupted by a crash (started, never finished): does it count against the task's re-attempt allowance (F-10d = 1)? | (a) Mark it `interrupted`; the re-run gets the next attempt number but does **not** use F-10d. A separate cap `orchestration.max_crash_resumes` (default 3) per task: beyond it the task becomes **Blocked** ("repeated crashes") instead of looping. (b) Count it as a failed attempt | **(a).** A crash is not the agent's fault. Counting it would turn one laptop restart into a failed task. The cap still prevents endless crash loops |
| **D-41** | Worker process model | (a) A separate process, `uv run python -m devteam_worker`, started by `scripts/dev.ps1` next to the API. The API only writes jobs and flags; it never runs the graph. One worker at a time, enforced by a database lease. (b) Run the graph in a background thread inside the API | **(a).** Matches roadmap §7 (`apps/worker`). An API crash cannot corrupt a run, and the worker can be killed and restarted on its own, which is what AC-D-02 tests |
| **D-42** | KI-013: extraction currently runs inside the upload request (≈39 s for a 300-page PDF). Should D move it into the worker? | (a) Leave Part B upload behaviour unchanged. The graph's intake node extracts only documents still pending (none in normal use). (b) Change uploads to queue extraction in the worker | **(a).** It keeps approved Part B behaviour intact (your instruction: do not modify A–C unnecessarily). Option (b) can be a later, separately approved change |
| **D-43** | KI-019 (the audit-log tail can be cut by someone with file access who also rewinds the head) was noted "closable in D with an external copy of the head hash" | (a) After each stage, the worker copies the audit head (`seq` + hash) into the separate checkpoint database. `scripts/verify_audit.py` and `GET /api/audit/verify` cross-check it. (b) Store it in Windows Credential Manager. (c) Defer | **(a), small and optional.** It raises the bar, because an attacker must now alter two files consistently. It does not fully close the gap: nothing local can stop an attacker who controls the whole account. Say "defer" and it moves out of D |

---

## 3. Architecture

### 3.1 Package layout and dependency rules (roadmap §7.1)

| Package | New / existing | May import | Must not import |
|---|---|---|---|
| `packages/orchestration` (`devteam_orchestration`) | **New** | core, state, harness, services, agents, `langgraph` | apps |
| `apps/worker` (`devteam_worker`) | **New** | orchestration, harness, state, core | agents directly, api |
| `apps/api` | Existing | core, state, harness (IAM, approvals), **orchestration run-command interface only** (writes jobs and flags; never compiles or runs a graph) | agents, `langgraph` |
| `agents/*` | Existing | core, `devteam_harness.agent_sdk` only (unchanged) | `langgraph`, orchestration, other agents |
| `packages/harness`, `state`, `core`, `services` | Existing | unchanged | **new rule:** `devteam_orchestration`, `devteam_worker`, `langgraph` |

Import-linter gets four new contracts: harness/state/core/services cannot import orchestration or worker; only orchestration and worker may import `langgraph`; the API cannot import agents or `langgraph`; and the httpx contract is extended to orchestration and worker. The `agents/ruff.toml` banned-API list gains `langgraph` and `langchain_core`. Each new rule gets a planted-violation test, as in Part C.

**Head Agent internals stay inside the Head package, as pure functions.** `agents/head/src/devteam_agents/head/{validator,scheduler,dispatcher,failure_router,template_planner}.py` import only `devteam_core` contracts and the standard library (`graphlib`). They receive everything as arguments, including the other roles' write scopes, so the Head never imports another agent. Orchestration calls them.

### 3.2 Node list with kinds (exactly 10 agent nodes, AC-D-05)

Every node carries `metadata={"node_kind": …}` and a member of the closed `NodeName` enum.

| Subgraph | Node (`NodeName`) | Kind | What it does in D |
|---|---|---|---|
| parent `project_run` | `route_entry` | router | `initial` → discovery; `change_request` → change_request (refused by the API in D) |
| | `route_after_discovery`, `route_after_build` | router | Continue, or end the run as cancelled/blocked/failed |
| | `finalize_run` | service | Writes the terminal run status through the state machine and closes the trace |
| **discovery** | `intake` | service (**real**, B extractor) | Extracts any document not yet extracted (`Send` fan-out, D-27). Already-extracted documents are skipped |
| | `brd_analyst` | **agent 1** | Stub: fixture analysis that refers to the project's real chunk IDs |
| | `feasibility` | service (stub) | All fixture technologies "supported" (test fixtures can make one unsupported) |
| | `feasibility_decision` | gate | `interrupt()`: proceed / cancel (D-01 (a)) |
| | `requirements_finalizer` | **agent 2** | Two operations in one node: `draft_questions`, then `merge_and_summarise`. Writes a **draft** `spec_versions` row with its checksum |
| | `clarification_form` | gate | Only if the stub reports critical gaps, and **at most once** (router + ledger) |
| | `project_approval` | gate | Approve / cancel, bound to the draft spec version + checksum (HC-13) |
| | `freeze_spec` | service (stub content, real record) | Verifies the approval, marks the spec version `frozen`, sets `RunState.spec_version_id/spec_checksum`. **The only writer of those fields** |
| **build** | `build_entry_guard` | guard | Re-reads the approval and spec version from SQLite and recomputes the checksum. Rejects and audits a mismatch → run **Blocked** |
| | `head` | **agent 3** | Stub behaviour: template planner → validator → validated `TaskPlan` (no LLM in D) |
| | `schedule` | router (Head scheduler + dispatcher) | Picks the next ready task (sequential) or service step and writes the dispatch record; the task goes READY → RUNNING |
| | `architect`, `database_developer`, `backend_developer`, `frontend_developer`, `tester`, `debugger_reviewer` | **agents 4–9** | Stub: fixture output, plus one small placeholder file written through `ctx.tools` inside the agent's write scope, headed "STUB — generated without AI" |
| | `integration_build` | service (stub) | Records "not run: sandbox arrives in Part E". Runs nothing |
| | `test_runner` | service (stub) | Outcome `not_run` (stub). **Never creates a `TestRun` or evidence row.** A test-only fixture can return "failing" to exercise the repair route |
| | `verify_task` | guard | Post-task check: declared output refs exist and the task status transition is legal |
| | `failure_router` | router (Head failure router) | Rules table → retry / repair task for the Debugger / Blocked / waiting for resources |
| **delivery** | `completion_gate_1`, `completion_gate_2` | service (stub) | Write **no** project verification status; report "stub: not evaluated" (D-17 two stages kept as positions) |
| | `documenter` | **agent 10** | Stub fixture output plus a placeholder `generated/docs/README.stub.md` |
| | `packager` | service (stub) | Records "package not built (Part O)" |
| | `publish_approval` | gate | Approve / cancel (roles project_user + reviewer, `config/approvals.yaml`) |
| | `github_deployer` | service (stub) | **No network code at all.** Records "not published: stub run" |
| | `final_report` | service | Writes the run summary record: stages, tasks, blockers, and "nothing verified" |
| **change_request** (skeleton) | `change_intake`, `change_impact` | service (stub) | Compiled and topology-tested only |
| | `change_approval` | gate | Skeleton |
| | `freeze_change_spec` | service (stub) | Hands over to the **same** build subgraph (no duplicate agent nodes) |

**Agent nodes:** `brd_analyst`, `requirements_finalizer`, `head`, `architect`, `database_developer`, `backend_developer`, `frontend_developer`, `tester`, `debugger_reviewer`, `documenter`. That is exactly one per `AgentRole`. The change subgraph reuses the build subgraph rather than declaring its own agent nodes.

### 3.3 Allowed-transition table

The table lives in `devteam_core.contracts.orchestration.ALLOWED_TRANSITIONS: Mapping[NodeName, frozenset[NodeName]]`.

Every routing function is pure and returns a `NodeName`. A wrapper checks each actual transition against the table: a violation raises `IllegalTransitionError` and writes `orchestration.illegal_transition` to the audit log. A conformance test proves that the compiled graph's edges equal the table, with nothing extra and nothing missing.

| From | Allowed next |
|---|---|
| `route_entry` | `intake`, `change_intake` |
| `intake` | `brd_analyst` |
| `brd_analyst` | `feasibility` |
| `feasibility` | `feasibility_decision` |
| `feasibility_decision` | `requirements_finalizer`, `finalize_run` (cancel) |
| `requirements_finalizer` | `clarification_form` (first pass, critical gaps, round unused), `project_approval` |
| `clarification_form` | `requirements_finalizer` (merge pass) |
| `project_approval` | `freeze_spec`, `finalize_run` (cancel) |
| `freeze_spec` | `route_after_discovery` |
| `route_after_discovery` | `build_entry_guard`, `finalize_run` |
| `build_entry_guard` | `head`, `finalize_run` (blocked) |
| `head` | `schedule`, `finalize_run` (plan rejected → blocked) |
| `schedule` | the 6 build agent nodes, `integration_build`, `test_runner`, `route_after_build` (all tasks terminal) |
| each build agent node, `integration_build`, `test_runner` | `verify_task` |
| `verify_task` | `schedule`, `failure_router` |
| `failure_router` | `schedule`, `finalize_run` (blocked/failed) |
| `route_after_build` | `completion_gate_1`, `finalize_run` |
| `completion_gate_1` → `documenter` → `packager` → `completion_gate_2` → `publish_approval` | (linear) |
| `publish_approval` | `github_deployer`, `final_report` (declined) |
| `github_deployer` | `final_report` |
| `final_report` | `finalize_run` |
| `change_intake` → `change_impact` → `change_approval` → `freeze_change_spec` → `route_after_change` | then `build_entry_guard`; cancel (at `change_approval`) → `finalize_run` |

**Correction made in D.1 (2026-11-23):** the first draft of this table sent a gate **cancel** to `route_after_discovery`. That router can also continue to `build_entry_guard`, so on paper a path into Build existed that skipped the spec freeze, held shut only by the router's runtime choice. The D.1 topology test found it. Every cancel now goes straight to `finalize_run`, so `route_after_discovery` and the new `route_after_change` are entered **only** from a spec-freeze node (tested, with the old edge kept as a negative control).

**Topology guarantee (AC-D-03, scenario #7):** no path exists from `route_entry` to any build node (`head`, the build agents, the build services) that avoids `freeze_spec` (or `freeze_change_spec`) **and** `build_entry_guard`. This is checked on the compiled graph.

### 3.4 Gates

| Gate node | Approval type (HC-13) | Required role | In D | Resume payload |
|---|---|---|---|---|
| `feasibility_decision` | `feasibility_decision` | project_user | Live | `{"approval_id"}` only |
| `clarification_form` | (form submission, once) | project_user | Live only when the fixture has critical gaps (always tested) | `{"submission_id"}` |
| `project_approval` | `spec_approval` (bound to the draft spec version + checksum) | project_user | Live | `{"approval_id"}` |
| `publish_approval` | `publish_github` | project_user + reviewer | Live (stub deployer) | `{"approval_id"}` |
| `change_approval` | `spec_approval` (new version) | project_user | Skeleton | — |

**Gate rules:**
1. **No side effects before `interrupt()`.** LangGraph re-runs the node from the start on resume.
2. The API validates a decision through `ApprovalService.record()`: role, pending gate, run, and spec version + checksum. Only after that does it queue a resume job.
3. After resuming, the node **re-reads** the approval by ID and calls `ApprovalService.verify()`. It never trusts the payload, so a forged or replayed ID is rejected and audited.
4. The existing unique `decision_key` prevents a double submit.

### 3.5 Idempotency keys (roadmap §6.7, R6)

| Node kind | Key | Behaviour on resume / re-run |
|---|---|---|
| Agent node | `run_id:node:task_id:attempt` (existing `task_attempts`) | `completed` → skip and return the stored result refs. `started` without a finish → mark `interrupted` (D-40) and run the next attempt. Each attempt's audit and file events carry the key |
| Service node with an effect (freeze, plan persistence, final report) | `run_id:node:scope:seq` in a new `node_effects` table, with two phases: `intent` → `performed` → `confirmed` | `confirmed` → skip. `intent`/`performed` → reconcile: check the DB for the effect, then finish or redo it. Used later for external actions (P) |
| Head plan | `run_id:head:plan:<spec_version_id>` | The plan is persisted once; a new plan is only created by the failure router with a new key |
| Gate | none before `interrupt()`; the decision is keyed by the approval's `decision_key` | Re-entering waits again or reads the recorded decision |
| Progress event | `run_id:seq` (monotonic) | The SSE replay is exact; there are no duplicate event IDs |

### 3.6 Worker design (LG-07, D-41)

1. **Start** (`python -m devteam_worker`). It fails closed if any of the following is not true:
   - settings are valid;
   - the keyring is available (D-18);
   - the agent registry holds exactly 10 specs and 10 behaviours (L2 startup check);
   - LangSmith/LangChain tracing environment variables are absent or false;
   - the checkpointer opens;
   - the migration head is current.
2. **Lease.** It takes the single `worker_lease` row (owner, pid, heartbeat). If another live worker holds it, the new worker refuses to start and logs why. A lease older than `lease_ttl_s` counts as dead.
3. **Recovery on start:**
   - runs still `running` with a stale heartbeat get a `resume` job;
   - ledger attempts left `started` are marked `interrupted`;
   - `node_effects` rows left at `intent` or `performed` are reconciled.
4. **Loop.** It polls `run_jobs` (`start`, `resume`, `gate_decision`). **One run executes at a time** (Spec §7.2).
   - The graph runs with `graph.stream(…, config={"configurable": {"thread_id": run_id}, "recursion_limit": …})`.
   - After every step it reads the run's control flag:
     - `pause` → **Paused**;
     - `cancel` → **Cancelled**.

     The final checkpoint is kept in both cases (cooperative cancel, roadmap §6.4).
   - An interrupt → **Waiting for human**.
   - `ResourceUnavailableError` → **Waiting for resources**, with a delayed resume job.
5. **Heartbeat.** A thread updates the lease and `runs.heartbeat_at` every `heartbeat_interval_s`, and records a RAM sample (psutil) as a metric event.
6. **Stop.** On Windows there is no graceful SIGTERM for console processes, so the worker also watches a stop event and finishes at the next node boundary. A hard kill is safe by design: that is the recovery test.

New settings (`config/defaults.yaml`, `orchestration:` section, validated, unknown keys rejected):
- `recursion_limit` 200;
- `poll_interval_s` 1;
- `heartbeat_interval_s` 5;
- `lease_ttl_s` 30;
- `max_crash_resumes` 3;
- `resource_wait_s` 60.

### 3.7 Run control API (LG-08)

Every endpoint requires sign-in and the `project_user` role, is rate-limited and audited, and keeps the existing origin checks.

| Method | Path | Effect |
|---|---|---|
| POST | `/api/projects/{project_id}/runs` | Start a stub run (`mode: "stub"`, D-38). Refused if the project already has an active run or has no extracted document. `run_kind: "change_request"` → 409 "available in Part Q" |
| GET | `/api/projects/{project_id}/runs`, `/api/runs/{run_id}` | List runs; run detail (status, stage, current node/task, pending gate, blockers) |
| POST | `/api/runs/{run_id}/pause`, `/resume`, `/cancel` | Set the control flag or queue a resume job; transitions go through `devteam_state.machines` |
| GET | `/api/runs/{run_id}/gate` | The pending gate and its stub summary |
| POST | `/api/runs/{run_id}/gates/{gate}/decision` | Validate through HC-13, record the approval, queue a resume job with the approval ID |
| GET | `/api/runs/{run_id}/tasks` | The plan and task statuses, distinguishing "agent returned output" from "harness verified" (Spec §20.2) |
| GET | `/api/runs/{run_id}/events` | **SSE** (`text/event-stream`, `Last-Event-ID` replay). Also `?after=<seq>&format=json` for tests |
| GET | `/api/runs/{run_id}/trace`, `/history`, `/metrics` | Span tree (allowlisted attributes); status/stage history from the audit; durations, tokens, RAM, queue depth |

SSE uses FastAPI's `StreamingResponse`, so no new dependency is needed. Same-origin `EventSource` GETs carry the existing HttpOnly cookie. The rate limiter counts a connection once, not per message.

### 3.8 Contracts added or changed (C.1 snapshot rules: version bump + snapshot update)

- **New module** `devteam_core.contracts.orchestration`:
  - `NodeName`, `NodeKind` (agent/service/gate/guard/router), `ALLOWED_TRANSITIONS`, `AGENT_NODE_FOR_ROLE` (closed map, 10 entries);
  - `RunMode` (`stub`; `real` reserved for F);
  - `GateDecision` (decision: approved/rejected/cancelled, F-4);
  - `DispatchRecord`;
  - `PlanValidationReport` (ok + list of `{code, task_id, detail}`).
- **`events`**: new event classes `NodeStarted`, `NodeFinished`, `GateOpened`, `RunProgress`, `MetricSample`. These are additive (new schemas), so existing schemas are not bumped.
- **`RunState` → `schema_version` 2:** adds `run_mode` and `clarification_round_used: bool` (the once-only rule must survive a restart). The change is recorded in the tracker; the snapshot is regenerated.
- The graph's state type is a `TypedDict` mirror in `devteam_orchestration.state`. Every node update is validated against `RunState`, so nothing but references can enter a checkpoint.

### 3.9 Data: migration `0005_orchestration`

Upgrade **and** downgrade are tested. `devteam_state.backup` runs automatically before migrating your real database.

| Table / column | Purpose |
|---|---|
| `runs` + `mode`, `current_node`, `pending_gate`, `control_request` (none/pause/cancel), `trace_id` | Run control and display |
| `run_jobs` (id, run, kind, payload refs, status, available_at, attempts) | Worker queue |
| `worker_lease` (single row) | Single-worker lock + heartbeat |
| `run_events` (run, seq, type, payload of allowlisted fields, ts; unique run+seq) | Progress events for SSE replay and the progress page |
| `node_effects` (key unique, run, node, phase, result_ref) | Two-phase idempotency for service effects |
| `spec_versions` (D-39) | Draft/frozen spec version + checksum checked by the guard |
| `task_plans` (plan id, run, spec version, payload, source `template`, validation report) | The validated Head plan |
| `task_attempts.status` gains `interrupted` | D-40 (string column; no type change) |
| `audit_anchors` in `checkpoints.db` | D-43 only, if approved |

`data/checkpoints.db` stays separate from `data/state.db` (roadmap §6.1). Both use WAL and the existing busy timeout.

---

## 4. Gaps found in completed parts (reported, not silently fixed)

| Gap | Where | Plan |
|---|---|---|
| Working Plan LG-01 says `NodeName` and the allowed-transition table were to be frozen in C. C delivered `Stage` and `GateName` but **not** `NodeName` or the table | `devteam_core.contracts` | Added in D.1 as a new contract module (additive). No change to approved C behaviour |
| `RunState` has no field to remember that the single clarification round was used | `contracts/state.py` | `schema_version` 2 (§3.8) |
| `task_attempts.task_id` is required, so service nodes cannot use the attempt ledger | migration 0003 | New `node_effects` table instead of altering the applied migration (CLAUDE.md: never edit an applied migration) |
| There is no `AGENTS.md` in the repository (your request mentioned one). CLAUDE.md is the agent instruction file | repository root | No action unless you want one; CLAUDE.md already covers it |

---

## 5. Files

**Create:**
- `packages/orchestration/pyproject.toml`
- `packages/orchestration/src/devteam_orchestration/`:
  - `graph.py` (parent graph);
  - `state.py`;
  - `routing.py`;
  - `guards.py` (build-entry guard, verify task);
  - `nodes/{agent.py, service.py, gate.py, wrappers.py}` (tracing, audit, transition check, ledger);
  - `subgraphs/{discovery, build, delivery, change_request}.py`;
  - `checkpointer.py`;
  - `ledger.py` (two-phase helper);
  - `registry.py` (the 10 specs + stub behaviours, startup check);
  - `services/` (stub services);
  - `commands.py` (run-command interface used by the API);
  - `events.py`;
  - `diagram.py`.
- `apps/worker/pyproject.toml`
- `apps/worker/src/devteam_worker/{__init__, __main__, main.py, queue.py, lease.py, heartbeat.py, recovery.py}.py`
- `agents/head/src/devteam_agents/head/{validator, scheduler, dispatcher, failure_router, template_planner}.py`
- `agents/<each of 10>/src/devteam_agents/<name>/stub.py`
- `packages/harness/src/devteam_harness/llm_gateway/providers/fixture.py`: fixture responder, one builder per D operation, producing schema-valid v1 outputs from the request's data block
- `packages/core/src/devteam_core/contracts/orchestration.py`, plus the new schema JSON files
- `packages/state/src/devteam_state/migrations/versions/0005_orchestration.py`
- `packages/state/src/devteam_state/repositories/{orchestration.py, spec_versions.py}`
- `apps/api/src/devteam_api/routers/runs.py`
- `apps/api/src/devteam_api/sse.py`
- `apps/web/src/pages/{BuildProgressPage, GatePage, TracePage, RunHistoryPage}.tsx`
- `apps/web/src/pages/PartD.test.tsx`
- Tests:
  - `tests/integration/orchestration/*`;
  - `tests/security/test_orchestration_*.py`;
  - `tests/e2e/test_fake_full_run.py` (`slow`, E2E-2);
  - unit tests inside `agents/head/tests/` and `packages/orchestration/tests/`.
- `scripts/demo_part_d.py`
- `docs/architecture/orchestration.md` (diagram generated from the compiled graph with `draw_mermaid()`, which works offline; **never** `draw_mermaid_png`, which calls a web service)
- `docs/runbooks/worker.md`
- `docs/parts/PART-D-COMPLETION-REPORT.md` (at the gate)

**Modify (smallest change; existing behaviour preserved):**
- Root `pyproject.toml`: two workspace members, import-linter contracts, coverage paths.
- `agents/ruff.toml`: ban `langgraph` and `langchain_core`.
- `config/defaults.yaml` + core settings model: `orchestration` section.
- `config/guardrails/policies.yaml`: a write rule for the stub placeholder files, inside each build agent's existing write scope.
- `packages/state` models and the runs repository: new columns and tables.
- `packages/harness/.../llm_gateway/providers/__init__.py`: register the fixture provider for stub runs only.
- `apps/api/src/devteam_api/main.py`: router registration.
- `apps/api/openapi.json`, `apps/web/src/api/schema.d.ts` (regenerated).
- `apps/web/src/App.tsx`, `components/Layout.tsx`: routes and nav. `pages/ProjectPage.tsx`: a "Start stub run" button and a runs list.
- `scripts/dev.ps1`: also start the worker.
- `scripts/check_agent_count.py`: none expected (the L4 check is a test).
- `scripts/verify_audit.py`: D-43 only.
- README, CHANGELOG, `docs/architecture/harness.md` (links), trackers.

**Not touched:** Part B upload/extraction behaviour (D-42 (a)), the Part C approval/policy/budget logic, and existing migrations.

---

## 6. Dependencies to add

These go in `packages/orchestration/pyproject.toml` only; the worker gets them through orchestration. The constraints are taken from PyPI on 2026-11-23 and will be re-checked at D.1.

| Package | Constraint | Licence | Reason |
|---|---|---|---|
| `langgraph` | `>=1.2.24,<1.3` | MIT | HC-07 (mandatory stack, ADR-0001) |
| `langgraph-checkpoint-sqlite` | `>=3.1.3,<3.2` | MIT | `SqliteSaver` on `data/checkpoints.db` (LG-05) |

**Transitive dependencies:**
- `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk`;
- `langchain-core` (MIT), which pulls `langsmith`, `httpx`, `tenacity`, `jsonpatch`, `uuid-utils`;
- `ormsgpack`, `xxhash`, `aiosqlite`, `sqlite-vec`.

**Gate in D.1 before any code uses them:**
- `pip-audit --strict` (runtime and dev) must be clean;
- every licence must be permissive (MIT/Apache/BSD; **no AGPL**, NFR-013, CLAUDE.md §10).

If either check fails, I stop and report. No other new dependency is planned: SSE uses FastAPI, the diagram uses LangGraph's built-in Mermaid text, and `psutil` is already present.

---

## 7. Tests mapped to acceptance criteria

| AC | Tests |
|---|---|
| **AC-D-01** The fake end-to-end run completes | **E2E-2** (`slow`): API + a real worker subprocess. Create project → upload sample BRD → start stub run → decide the three gates through the API → status **completed**, stage timeline complete, final report says "stub: not verified", no `TestRun` rows and no verification status written. Events are received over SSE in order with no gaps. Plus an in-process variant for the fast suite |
| **AC-D-02** Kill/restart recovery without duplicated side effects | Spawn the worker. A test hook in the **fixture provider** blocks on a named event at a chosen node (deterministic, no sleeps). Kill the process (psutil), restart, and let it finish. Assert: every completed ledger key appears **once**; the interrupted attempt is `interrupted` (D-40); each placeholder file has one `file.changed` event per completed attempt; plan, freeze and final report effects exist exactly once; the audit chain verifies. Also: crash at a gate, crash inside a two-phase service, and `max_crash_resumes` exceeded → Blocked |
| **AC-D-03** Build unreachable without approval (topology **and** runtime) | Topology: no path to any build node avoiding `freeze_spec` + `build_entry_guard` (compiled-graph search). Runtime cases, each **rejected and audited**:<br>• forced `Command(goto="architect")` / `update_state(as_node=…)`;<br>• approval missing;<br>• approval for another run;<br>• checksum altered in `spec_versions`;<br>• spec version still draft;<br>• approval with the wrong role. |
| **AC-D-04** Cyclic, invalid or uncovered plans rejected | Validator unit tests (each must report the specific error code):<br>• cycle (`graphlib.CycleError`, cycle path reported);<br>• unknown agent;<br>• discovery role in a build plan;<br>• missing dependency;<br>• uncovered requirement;<br>• `permitted_paths` outside the role's write scope (including `..` and other-agent scopes);<br>• duplicate IDs;<br>• mixed spec version;<br>• empty plan.<br>Integration: an injected bad plan → `plan.rejected` audited → run **Blocked** with the reasons. A hypothesis property test: every plan the template planner produces validates |
| **AC-D-05** The compiled graph exposes exactly 10 agent nodes (L4) | Set of `node_kind == "agent"` nodes == `AgentRole` (one-to-one) across all subgraphs. Every node has a `node_kind`. Graph edges == `ALLOWED_TRANSITIONS`. A planted 11th agent node in a temporary graph makes the test fail (negative control) |
| **AC-D-06** Spec scenarios #7, #10, #23 in stub form | **#7** = AC-D-03 runtime cases ("start architecture without approval → harness rejects the transition"). **#10** = AC-D-04 integration ("cyclic or invalid plan → validator rejects it"). **#23** = AC-D-02 restart from the latest valid checkpoint |

**Also covered (Working Plan LG-10/LG-11 and HS-17/HS-18):**
- **Routing and scheduler** (pure, table-driven):
  - every routing function against the transition table;
  - the scheduler is sequential: dependency order, deadlock → Skipped/Blocked with a reason, all terminal → `route_after_build`;
  - failure-router rules per error category (F-10c: policy/budget never retried; F-10d re-attempt bound exact).
- **Gates and run control:**
  - interrupt → API decision → resume; the clarification form at most once (including after a restart);
  - pause, resume and cancel at node boundaries, with the final checkpoint kept;
  - retry limit; LLM single-call lock under the worker (no overlap).
- **Worker:**
  - a second worker refuses to start; stale-lease takeover;
  - waiting for resources (low-RAM probe) → resumes later.
- **Observability:**
  - every node produces a span (parent run → node → executor → model/tool) and audit events;
  - a full fake run is traced end to end.
- **Regression:** the Part A–C fast suite, security suite and static gates stay green. The web tests cover the new pages, using generated API types only.

---

## 8. Security

| Threat | Control | Test |
|---|---|---|
| Skipping approval | Topology, guard, per-tool stage check (three layers, §3.3–3.4) | AC-D-03 suite |
| Forged or replayed gate decision | The API validates through HC-13; the node re-reads and verifies the approval by ID; unique `decision_key` | Forged ID, other run, wrong role, double submit, stale checksum → rejected + audited |
| Tampered checkpoint or resume payload | References-only state, validated against `RunState` on every update; the serializer allows only the state types; **pickle fallback disabled** (past LangGraph deserialization advisories) | Crafted checkpoint blob refused; state with an extra field refused |
| Document text, prompts or secrets leaking into checkpoints, events, SSE or traces | References-only state; event payload allowlist; span attribute allowlist (D-30); redaction | Canary secret + canary BRD sentence through a full run, then scan `checkpoints.db`, `run_events`, SSE output, `trace_spans`, logs and audit: **zero hits** |
| Cloud telemetry via LangSmith (pulled in by `langchain-core`) | The worker refuses to start if `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` is true; the variables are forced off at start; no LangSmith client is constructed | Env-guard test; full fake run with outbound sockets blocked (loopback only) completes |
| Any network use by stubs (deployer, packager) | No network code; import-linter bans httpx outside the gateway providers | Socket-blocking E2E; import-linter planted violation |
| Unauthorised run control | Existing sign-in, role, origin checks, rate limit, audit | 401/403/cross-origin tests on every new endpoint, each audited |
| Two workers corrupting a run | DB lease + one run at a time | Second worker refuses; concurrent-start test |
| Agents reaching the graph | Agents receive only `AgentContext`; Ruff and import-linter ban `langgraph`/orchestration in `agents/**` | Planted-violation tests |
| Stub run mistaken for a verified result | Label everywhere; stub Test Runner writes no result; stub Completion Gate writes no status | E2E asserts no `TestRun`/evidence/verification rows; UI test checks the banner |

**Before each checkpoint commit:**
- the security suite;
- `gitleaks detect` on history and on the working tree (`--no-git`, excluding caches);
- `pip-audit` (after D.1);
- `npm audit` (D.5).

---

## 9. Demonstration (G-D)

1. `uv run python scripts/demo_part_d.py` uses a temporary data folder and does not touch your real data. It prints in plain words:
   - a stub run going through every stage and approved gate → **completed (stub, not verified)**;
   - the worker **killed** in the middle of the build, restarted → **resumed, no step repeated** (ledger summary);
   - a forced jump to the architect without approval → **refused, logged**;
   - a circular plan → **rejected** with the cycle named.
2. **Live in the app:** `scripts/dev.ps1` now starts the API, the web UI and the worker.
   1. Open a project and press "Start stub run".
   2. Watch the Build Progress page update live.
   3. Approve feasibility and the project; pause and resume once.
   4. Approve publish (nothing is published).
   5. Open the trace viewer and the run history.
3. Kill the worker window during a live run, start it again, and watch the progress page continue.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| LangGraph re-executes a node on resume (R6) | Ledger, `node_effects`, side-effect-free gates, crash tests at agent, gate and service nodes |
| LangGraph API changes (R7) | Pinned minor versions; all LangGraph use behind `devteam_orchestration` wrappers; topology contract tests catch changes |
| SQLite contention: API + worker + checkpointer (R8) | Separate checkpoint DB, WAL, busy timeout, short transactions; a concurrency test with the API polling during a run |
| Graph state and DB diverge (R9) | References only; `RunState` validation per update; recovery reads the DB, not the checkpoint, for domain facts |
| Kill test flaky on Windows | Deterministic stop point (named event in the fixture provider), not timing; generous timeouts; the process is killed with psutil |
| New network-capable transitive dependencies (`langsmith`, `httpx`, `langgraph-sdk`) | Env guard, socket-blocking E2E, import-linter; nothing imports them |
| Memory on this laptop (R2, KI-005) | Measure worker RSS during the E2E run and record it in the report. The worker loads no model in D |
| SSE through the Vite dev proxy may buffer | `Cache-Control: no-cache`, `X-Accel-Buffering: no`, a proxy setting test; JSON polling fallback in the page |
| Part D is large (five checkpoints) | Each checkpoint is tested, tracked and committed on its own. If one overruns, I stop at the boundary and report; tests are never cut to fit |
| Stub outputs mistaken for real work | D-38 labelling + the "never writes results/status" rules + E2E assertions |

---

## 11. Checkpoint breakdown

Each checkpoint follows the same procedure:
1. Write the tests first for the safety-critical parts: guard, gates, ledger, validator, lease.
2. Implement.
3. Run the full fast suite, the security suite and the static gates (Parts A–C stay green).
4. Add tracker rows (CH/TR, exact locations).
5. Add a progress-tracker paragraph.
6. Make one local commit on `part/d-orchestration`.

A checkpoint is not done with any failing test, any skip without a recorded reason, or any placeholder test. Every security test has a negative control.

| Checkpoint | Order of work | Exit criteria (measured) | Commit |
|---|---|---|---|
| **D.1 Foundations** | 1. Add the dependencies, `uv lock`, pip-audit and licence check (**stop if not clean**). 2. `contracts/orchestration.py`, the new events, `RunState` v2, schema export + snapshots. 3. Migration `0005_orchestration` (+ downgrade, backup) and repositories. 4. Settings `orchestration` section. 5. Import-linter contracts + Ruff bans + planted violations. 6. LangSmith env guard | Audits clean; contracts round-trip; snapshot current; migration up/down on a copy; every new import rule fails on its planted violation; A–C regression green | `feat(orchestration): D.1 contracts, migration, dependencies` |
| **D.2 Head core and stubs** | 1. Validator (tests first, every AC-D-04 case). 2. Scheduler, dispatcher, failure router (tables), template planner (+ property test). 3. Fixture responder (schema-valid outputs per operation). 4. Ten `stub.py` behaviours through the real executor + gateway (fake provider). 5. Stub services with honesty rules | Every validator case reports its code; template plans always validate; each stub returns a schema-valid output via the executor (audit, usage, spans present); stub services write no results/status | `feat(agents): D.2 head deterministic core and ten stubs` |
| **D.3 Graph** | 1. Node wrappers (span, audit, transition check, ledger, `RunState` validation). 2. Subgraphs + parent graph. 3. Build-entry guard and gates. 4. Checkpointer, two-phase helper, `Send` fan-out. 5. Topology tests (L4, reachability, table conformance). 6. Diagram doc | AC-D-03 (topology + runtime), AC-D-05; in-process fake run completes with approvals given in the test; resume after an in-process interruption repeats nothing | `feat(orchestration): D.3 graph, guards, gates, checkpointing` |
| **D.4 Worker and run control** | 1. Worker lease, queue, heartbeat, recovery. 2. Run control API + gate decision endpoint. 3. Kill/restart tests (agent, gate, service crash points). 4. Pause/resume/cancel, waiting for resources | AC-D-02, scenario #23; second worker refused; every endpoint authenticated, role-checked and audited | `feat(worker): D.4 run worker, run control, crash recovery` |
| **D.5 Progress, observability, UI** | 1. Run events + SSE + metrics. 2. Trace and history endpoints. 3. UI pages + Vitest. 4. OpenAPI + types regenerated. 5. E2E-2 with sockets blocked. 6. D-43 if approved | AC-D-01; canary scan zero hits; no outbound network; web lint/typecheck/test/build green | `feat(web): D.5 build progress, gates, trace viewer, run history` |
| **Gate G-D** | Full regression A–D; security suite; coverage (≥ 90% harness/contracts and the orchestration guard/ledger modules, ≥ 80% elsewhere); gitleaks; pip-audit; npm audit; real DB migrated with backup (read-only checks after); demo; completion report | All AC-D-01…06 with evidence | After your approval: merge + `v0.4.0` + `part-d-approved` |

**Order dependency:**
- D.2 needs the D.1 contracts.
- D.3 needs the D.2 stubs and Head core.
- D.4 needs the D.3 graph.
- D.5 needs D.4's events and API.

---

## Owner confirmation: ☑ D-27 (b) · ☑ D-38 · ☑ D-39 · ☑ D-40 (block after more than 3 consecutive crashes) · ☑ D-41 · ☑ D-42 · ☒ D-43 **deferred** · ☑ start implementation, D.1 only first (AP-015, 2026-11-23)
