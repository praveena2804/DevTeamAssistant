# Part C — Shared Contracts & Harness Core — Plan

Roadmap ref: §13 Part C · Milestone: M1 · Gate: **G-C** · Working Plan §20 Part C, HS-06…HS-16 · Date: 2026-11-19

| Field | Value |
|---|---|
| Status | **Approved** (AP-007 decisions, AP-008 F-1…F-11 with F-9d = 180 s). Implementation in progress, checkpoint by checkpoint |
| Preconditions | G-B approved (**done**, AP-006); D-18, D-18a, D-26, D-30, D-32 (**done**, AP-007); F-1…F-11 (**done**, AP-008) |
| Harness components | Establishes HC-01…HC-06, HC-11…HC-13, HC-15…HC-17, HC-19…HC-21 (interfaces where the roadmap says so) |
| Agents | **None implemented.** All 10 agent packages are created, but they hold only declarations (`AgentSpec`) and no behaviour |
| Branch | `part/c-harness-core` (local). One local commit per checkpoint C.1–C.4 |

---

## 0. Part C in simple words

Part C builds the **safety system** that every agent must go through. No agent exists yet. The point is to build the walls before anyone moves in.

When it is done:

1. **Every agent's "job description" is written down in code**: what it receives, what it must hand back, and which tools and folders it may use. There are exactly ten, and the system refuses to start with nine or eleven.
2. **Nothing is allowed unless a rule says so.** If an agent tries to use a tool it isn't allowed, write outside its folder (for example `..\..\Windows`), or act before the right stage, it is stopped, and the attempt is written to the log.
3. **Every agent has spending limits:** time, AI usage, number of attempts. When a limit is reached the work stops instead of running forever.
4. **Secrets (keys, passwords) go into Windows' own safe store** (Credential Manager), not into files, and they are blanked out of every log.
5. **The activity log becomes tamper-evident.** Each entry is "chained" to the previous one, so if anyone edits or deletes an old entry, a check reports exactly where.
6. **Approvals are enforced in code:** only the right role can approve, and an approval cannot be reused for a different version.
7. **The AI connection is a single, controlled doorway.** In Part C it is connected to a fake AI (for testing only). The real local models come in Part F.

To prove it, a test agent (a stand-in used only in tests, not an 11th agent) tries allowed and forbidden things. You will see the forbidden ones refused and logged.

What you will see at the end: a demo command that runs the stand-in agent and prints in plain words what was allowed, refused and logged. The Activity page also shows a **"log intact"** indicator.

---

## 1. Scope

**In scope (roadmap §13 C, four checkpoints under one gate, per D-32 (a)):**

| Checkpoint | Steps | What |
|---|---|---|
| **C.1 Contracts** | HS-06 | All shared data shapes (§3): base types, 22 domain records, `AgentTask`, `TaskPlan`, inputs/outputs for all 10 agents, events, tool and model call shapes, status enums, error categories, `RunState`. JSON Schema export plus snapshot tests; `schema_version` on each |
| **C.2 Identity, policy, secrets** | HS-07…HS-09 | Agent and service principals (HC-15). Deny-by-default policy engine plus input/output guardrails (HC-01). `SecretsManager` on the Windows keyring plus redaction registry (HC-17) |
| **C.3 Tools, audit, state** | HS-10…HS-12 | `ToolRegistry`/`ToolSpec` plus Controlled File Service with Windows-aware path safety (HC-06). Hash-chained audit with new event types (HC-21). Run/task state machines, repositories, attempt ledger (HC-19) |
| **C.4 Execution** | HS-13…HS-16 | Budget Manager plus usage counters (HC-05), retry policy (HC-12), Approval Service (HC-13), LLM Gateway interface plus `fake` provider (HC-02), prompt/skill registry interface (HC-16, D-36), tracing spans (HC-11). Agent SDK (`AgentSpec` with D-37 operation IDs, `AgentRole`, `AgentRegistry`, `AgentContext`) and **Agent Executor**. Ten agent packages (declarations only). Import-linter, banned-API rules and agent-count check made active |

**Out of scope:**

| Excluded | Comes in |
|---|---|
| LangGraph graph and real runs | D |
| Sandbox and command recipes | E |
| Real Ollama adapter, model routing, RAG/ChromaDB, context manager, prompt files | F |
| Any agent behaviour | D (stubs), H onward (real) |
| Approval UI screens and gate endpoints | I, N, P |
| Database tables for domain records (Requirement, Gap, …) | When their owning part first writes them (H onward). C defines their contracts only |
| GitHub tokens (the PAT half of D-18) | P |

---

## 2. Decisions

### 2.0 Ruled by the owner (AP-007, 2026-11-20)

| ID | Ruling | Conditions attached by the owner, and how the plan meets them |
|---|---|---|
| **D-32** | **(a) Approved.** Four separately tested and saved checkpoints, one gate G-C | Part D does not start until G-C is approved |
| **D-18** | **Approved.** Windows Credential Manager via `keyring` | **No plaintext secret-file fallback.** If the keyring is unavailable, the API refuses to start and says why (fail closed). The in-memory backend exists only inside the test suite (`test` profile) and is rejected by config validation in any other profile. Secrets are redacted from logs, errors (exception text included) and traces |
| **D-18a** | **Approved with conditions.** Move `data/api_token` into Credential Manager | Order of checks; **all** must pass before the file is deleted: (1) store succeeded; (2) read-back matches the original (constant-time compare); (3) the API authenticates a request with the stored key; (4) the existing sign-in link (`/signin#token=…`) still signs in. Any failure → stop, keep the file, report. The result is recorded (tracker and audit event `secret.migrated`) **without** the key, its hash or any prefix |
| **D-30** | **Approved.** OpenTelemetry, local storage only | No exporter other than the local SQLite exporter is installed or configurable; the OTLP exporter packages are not added; a test asserts no network exporter is registered. Span attributes pass an **allowlist** (IDs, names, counts, durations, outcome codes); values are also run through redaction; document text, prompts, model output, file contents, tokens and credentials are never attributes |
| **D-26** | **Approved.** `agents/<agent_name>/`, same conventions as the other packages | Exactly the ten canonical names; permissions and write boundaries are enforced by the harness (§4), not by the packages |

### 2.1 Field values and defaults (F-1…F-11): **APPROVED (AP-008, 2026-11-20), with F-9d = 180 s**

Approved by the owner on 2026-11-20 (AP-008) as proposed, except F-9d changed to 180 s. Each value lives in one place (a contract enum or `config/budgets.yaml`), so a later change is a one-line edit plus a contract version bump or config change. No code hardcodes a number.

**Correction to the first draft:** it proposed 8,000 context tokens per task. That was wrong for this laptop: the models were benchmarked with a **4,096-token** context window (`config/defaults.yaml` `resources.llm_context_tokens`, Spec §9.3 range 4,096–8,192), and the window must hold both the input and the answer. The figures below are derived from the Part A benchmark (`docs/benchmarks/2026-11-19-baseline.md`: about 12.5–13 tokens/s for the 3B models, about 2.1 GB Ollama memory, 24–30 s for a small structured answer).

#### F-1 … F-8: allowed values of record fields (contract enums)

| ID | Field | Allowed values | Default | Who may set it | Rationale | Depends on it |
|---|---|---|---|---|---|---|
| **F-1** | `Requirement.priority` | `must`, `should`, `could`, `wont`, `unspecified` | `unspecified` | BRD Analyst **only** when the BRD states it (with source ref); otherwise the user's clarification answer | MoSCoW is the common scale for BRDs. `unspecified` prevents the AI from inventing priority (Spec §5.4.1 "never assume"). Priority is **not** criticality: criticality is a separate code rule on Gaps | BRD Analyst (writes); Requirements Finalizer (sorts questions "by impact", Spec §5.4); Head Agent planner (ordering); Documenter; traceability report; approval screen |
| **F-2** | `Requirement.status` | `extracted` → `confirmed` → (`excluded` \| `blocked`) | `extracted` | `extracted`: BRD Analyst. `confirmed`: Spec Freeze service, after approval. `excluded`: the user's answer or an unanswered critical gap (Spec §21). `blocked`: Feasibility Gate | Describes **scope**, not progress. Build progress and verification live in `RequirementEvidence` (F-7), so no agent can mark a requirement verified (Spec §6.1) | Finalizer; Spec Freeze; Feasibility Gate; Head planner (only `confirmed` requirements get tasks); Completion Gate; Documenter |
| **F-3** | `BusinessRule.verification_status` | `unverified`, `tested`, `manual_verification`, `blocked` | `unverified` | `tested`: Test Runner + Completion Gate only. `manual_verification`: only with an approved reviewer record (Spec §18.2). `blocked`: Feasibility Gate / Completion Gate | Spec §18.2: every business rule has a test **or** a documented manual verification reason. No agent can set any value except via the harness services named | Tester (maps tests to rules); Test Runner; Completion Gate; Documenter |
| **F-4** | `Approval.decision` | `approved`, `rejected`, `cancelled` | none (required, no default) | Human principals with the role in `config/approvals.yaml`. **Never an agent** | Spec §5.4 "approve, edit or cancel": *edit* sends the draft back for changes and does not create an approval record; *rejected* covers the other gate types (publish, test maintenance, DB switch) | Approval Service; every gate (G, I, N, P, Q); build-entry guard; audit |
| **F-5** | `Artifact.type` | `document`, `source_file`, `test_file`, `contract`, `migration`, `config`, `report`, `package` | none (required) | Controlled File Service sets it from the write path, not the agent | Closed list derived from Spec §19 deliverables. Deriving it from the path stops an agent labelling a test file as source to slip past the Debugger's no-test-edit rule | File Service; Debugger diff-scope check (`test_file` protected); Packager; Documenter; traceability |
| **F-6** | `TestRun.test_type` | `unit`, `api`, `database`, `integration`, `ui`, `build`, `lint`, `typecheck`, `security` | none (required) | Test Runner only | Covers every check type named in Spec §6.6–§6.9 and §18.2 | Test Runner; Completion Gate; Tester; Documenter (TEST_REPORT.md) |
| **F-7** | `RequirementEvidence.status` | `passing`, `failing`, `not_run`, `manual`, `blocked` | `not_run` | Test Runner / Completion Gate; `manual` only with an approved reviewer record | `not_run` as default means nothing looks green until the harness has run it (Spec §18: honest status). Feeds the Spec §18.1 project statuses | Completion Gate; traceability view (UI); Documenter |
| **F-8** | `ChangeRequest.status` | `submitted`, `analysing`, `awaiting_approval`, `approved`, `rejected`, `applied`, `failed` | `submitted` | Change handler (Head Agent internals) for progress; Approval Service for `approved`/`rejected`; Completion Gate for `applied`/`failed` | Mirrors Spec §5.7 stages. **Provisional until Part Q**; C only needs the contract to exist | Head Agent change handler; Approval Service; Part Q |

#### F-9: budgets (`config/budgets.yaml`, low-memory profile)

Every limit is enforced by the Budget Manager **before** the next action, not after. Reaching a limit stops the task with category `budget_exhausted`, writes an audit event and never retries automatically. Config validation rejects values outside min–max, and rejects F-9b + F-9c above the model's context window.

| ID | Limit | Default | Min | Max | Rationale | Depends on it |
|---|---|---|---|---|---|---|
| **F-9a** | Answer length per model call (output tokens) | **1,024** | 128 | 2,048 | ≈ 80 s at 12.5 tok/s. Enough for one bounded structured record set or one source file chunk (Spec §6.2/§6.6 "one bounded chunk/file at a time") | Gateway (HC-02); every agent |
| **F-9b** | Input context per model call (tokens) | **3,072** | 1,024 | window − F-9a | 4,096 window − 1,024 answer. The Context Manager (F) must fit inside it | Context Manager; gateway; every agent |
| **F-9c** | Model context window (`num_ctx`) | **4,096** (existing setting) | 2,048 | 8,192 | Benchmarked value; Spec §9.3 range. Raising it costs RAM | Gateway; Ollama adapter (F) |
| **F-9d** | Time limit per single model call | **180 s** (owner ruling AP-008; first proposal was 300 s) | 30 | 900 | A full call (≈ 3k input + 1k output) takes ≈ 2–2.5 min on this laptop, so 180 s leaves little room for a cold model load: a timeout is treated as a transient error (F-10b) and then smaller-model fallback (F). Spec §21: too slow → smaller model or pause, not wait forever | Gateway timeout / fallback (HC-04) |
| **F-9e** | Model calls per task | **8** | 1 | 50 | A task is "one bounded unit" (one section / file group). Allows a few sub-steps plus the F-10 retries | Budget Manager; every agent |
| **F-9f** | Model tokens per task (input + output) | **32,768** | 4,096 | 200,000 | F-9e × (F-9b + F-9a) = 8 × 4,096 | Budget Manager; usage counters |
| **F-9g** | Wall-clock time per task | **30 min** | 1 min | 120 min | 8 calls × ≈ 2.5 min + tool time and headroom | Budget Manager; Head scheduler |
| **F-9h** | Tool calls per task | **200** | 10 | 1,000 | Generous for file reads/writes during build tasks; still catches loops | Tool layer; Budget Manager |
| **F-9i** | Size of one file write | **1 MB** | 64 KB | 10 MB | Generated source files are small; larger writes signal a fault | Controlled File Service |
| **F-9j** | Total bytes written per task | **10 MB** | 1 MB | 100 MB | Prevents a runaway task filling the disk | Controlled File Service; Budget Manager |
| **F-9k** | Active time per run | **12 h** | 1 h | 48 h | Estimate: ≈ 40 build tasks × ≈ 15 min on this laptop. Counts **active time only**; waiting for you or paused time is not counted | Budget Manager; run state machine |
| **F-9l** | Model tokens per run | **1,500,000** | 100,000 | 5,000,000 | ≈ 40 tasks × F-9f, plus discovery | Budget Manager |
| **F-9m** | Model calls per run | **600** | 50 | 3,000 | ≈ 40 tasks × F-9e, plus discovery and retries | Budget Manager |
| **F-9n** | Minimum free RAM to start a model call | **1,024 MB** | 256 | 8,192 | Benchmark low point was 460 MB free (risk of swapping). Below this the run pauses as `WAITING_FOR_RESOURCES` (Spec §21) instead of failing. The existing doctor warning (3,072 MB) is unchanged | Budget Manager / Resource Guard; run state machine |

#### F-10: retries

| ID | Limit | Default | Min | Max | Rationale | Depends on it |
|---|---|---|---|---|---|---|
| **F-10a** | Retries after **invalid AI output** (schema/guardrail failure), per model call | **2** (3 attempts) | 0 | 3 | Spec §21 "bounded retry; then fallback or block". Each retry sends the validation errors back. After the last one: smaller-model fallback (F) or task `failed`, with errors attached | Executor; gateway |
| **F-10b** | Retries after **transient errors** (timeout, connection reset, model busy) | **2** | 0 | 5 | Backoff 2 s, then 4 s (max wait 30 s), with jitter | Retry policy (HC-12); gateway |
| **F-10c** | Retries after **policy denial** or **budget exhaustion** | **0** (fixed, not configurable) | 0 | 0 | A refused action must not be retried automatically (Spec §21: "reject, audit, stop") | Executor; policy engine |
| **F-10d** | Whole-task re-attempts by the Head Agent after a task fails | **1** | 0 | 3 | Attempts are recorded in the attempt ledger. The repair loop (Debugger) has its own limit, decided in Part N | Head scheduler (D/J); attempt ledger |

#### F-11: fields added to `AgentTask` beyond Spec §6.4

| ID | Field | Type / allowed values | Default | Rationale | Depends on it |
|---|---|---|---|---|---|
| **F-11a** | `run_id` | ID `run_…` | required | A project has several runs (initial + change requests). Every task must belong to exactly one | State machines; attempt ledger; audit; tracing |
| **F-11b** | `spec_version_id` | ID `spv_…` or empty | empty for discovery tasks; **required** for build tasks | Lets the stage guardrail verify "build only against an approved, frozen spec" on every tool call (roadmap §6.5 layer 3) | Policy engine (stage rule); build-entry guard; Head planner |
| **F-11c** | `operation_id` | `<agent>.<operation>`, from the agent's `AgentSpec` list | required | D-37 per-operation model routing (`docs/architecture/MODEL_ROUTING.md`) | Gateway / router (F); usage per operation |
| **F-11d** | `budget_profile` | name from `config/budgets.yaml` | `default` | Lets later parts give heavy agents (e.g. Backend) a larger profile without changing code. Never larger than the run budget | Budget Manager |

Everything else in §3 maps directly to Spec §6, §11, §12, §18 and §20.2, or to roadmap §5–§6.

---

## 3. Contracts (C.1), `devteam_core.contracts`

Every model is Pydantic v2, `extra="forbid"`, frozen where it is a record, and carries `schema_version: Literal[1]`. IDs use the existing `new_id(prefix)` scheme.

| Module | Contents (fields from the cited source) |
|---|---|
| `base` | `SourceRef` (document_id, chunk_id, page_start/end, section, paragraph_start/end, display: Part B reference format). `Provenance` = `brd_fact` / `user_answer` / `assumption` / `recommendation` (Spec §6.1). `Sourced` mixin: provenance + source_refs (required when `brd_fact`) |
| `records` | Spec §12 entities with the Spec §12 fields: Requirement (FR/NFR), BusinessRule, Role, Permission, EntityDefinition (attributes, relationships), Workflow (steps), Integration, TechnologyNeed, AcceptanceCriterion, Constraint, Gap (category = the 13 Spec §6.2 checklist items; criticality `critical`/`minor`, Spec §5.4.2), FeasibilityItem (Spec §5.3 four statuses), ClarificationQuestion/Answer, SpecVersion, DecisionRecord, Artifact, TestRun, RequirementEvidence, ChangeRequest, Approval, SecretReference |
| `tasks` | `AgentTask`: all Spec §6.4 fields + F-11. `TaskPlan`: tasks + plan-level checks (no cycles, known agents, dependencies exist). These are structural only; Part J adds the planner |
| `agents` | One input model and one output model per agent, **v1**, derived from each agent's "Inputs"/"Outputs" in Spec §6.2–§6.11. Outputs carry **references** (record IDs, artefact paths + checksums), never free text claiming results |
| `tools` | `ToolCall` (tool, version, args, principal, task_id), `ToolResult` (ok/denied/error, value, audit_ref), `SideEffect` enum (none / read / write_workspace / execute / external) |
| `llm` | `ModelRequest` (role, operation_id, slot, messages, output schema name, params, prompt id + version + hash), `ModelResponse` (parsed output, usage, model id, duration, validation result). No raw prompt text is ever written to audit |
| `events` | Progress/domain events (run/task transitions, tool and model calls, approvals, file changes) |
| `statuses` | `RunStatus` (roadmap §6.4), `TaskStatus` (Spec §20.2, 9 values), `ProjectVerificationStatus` (Spec §18.1, 8 values), `FeasibilityStatus` (4), `Stage`, `GateName` |
| `errors` | Error categories (HC-12): `retryable`, `invalid_output`, `budget_exhausted`, `policy_denied`, `resource_unavailable` (becomes WAITING_FOR_RESOURCES), `fatal`; `ErrorRef` |
| `state` | `RunState` (roadmap §6.8 fields), references only |

Exported schemas go to `packages/core/src/devteam_core/contracts/schemas/*.json` via `scripts/export_contract_schemas.py`. A test fails if they are out of date (the same pattern as `openapi.json`).

---

## 4. Design

### 4.1 Identity and policy (C.2)

- **Principals:** `agent:<role>:<task_id>` is issued per task by the executor and never by the agent itself; `service:<name>`; the existing human principals. Agents hold **no approval rights** (Spec §4).
- **Policy engine:** deny by default. A call is allowed only if a rule in `config/guardrails/policies.yaml` matches on **role × tool × path × stage**, the path is within `AgentSpec.write_paths ∩ task.permitted_paths`, and the path is not in `protected_paths.yaml` (frozen files, platform source, uploads). Every decision returns allow/deny plus the rule ID, and denials are audited.
- **Guardrails:**
  - **Input** (before a model call): size limits; BRD text wrapped and labelled as *data*; injection-pattern flagging, which records a warning and never executes anything.
  - **Output** (after a model call): schema validation; provenance required; secret detection; **forbidden claims** (output asserting "tests passed", "Verified" or results the harness did not record is rejected).
  - **Stage** (build tools require an approved spec version): the rule type exists and is tested in C. The real build tools come in E.
- **Secrets:** `SecretsManager` (`keyring`, Windows Credential Manager). The database and config hold only references (`secret://platform/<name>`). Every value read is registered for redaction. Secrets are never passed to the gateway, which checks for them. Tests use an in-memory backend; one test, marked to run locally only, touches the real Credential Manager.

### 4.2 Tools, audit, state (C.3)

- **ToolSpec** (Spec §11.2, all eight attributes): name, version, input/output models, allowed roles, allowed paths, timeout, side-effect class, approval requirement, audit behaviour. Calling an unregistered tool raises an error and is audited.
- **Tools built in C:**
  - `read_project_file`, `write_project_file`, `list_project_files` (Controlled File Service);
  - `read_extracted_document` (Part B chunks, read-only);
  - `search_project_knowledge` (interface only, answers "not available yet" until F).
  - There is no shell tool, ever.
- **Controlled File Service** (Windows-aware), which rejects:
  - absolute paths, drive letters, UNC (`\\server\share`) and `..` (also URL-encoded);
  - alternate data streams (`::$DATA`), reserved names (`CON`, `NUL`, …) and trailing dots or spaces;
  - case-variant escapes;
  - symlinks and **junctions** (reparse points), checked on every path component after resolution.

  It also enforces size limits, writes atomically (temp file plus replace), records a SHA-256 before and after, attaches a unified diff to the audit event, and protects frozen files.
- **Hash-chained audit:** a new `seq` column (strictly increasing), plus `prev_hash` and `hash` = SHA-256 of `prev_hash` and the canonical JSON of the row. Appends are serialised in a `BEGIN IMMEDIATE` transaction.
  - **Existing Part B rows are not modified.** The migration writes one **anchor event** whose hash covers all earlier rows, so the old rows are protected too and nothing is rewritten.
  - `verify_chain()` reports the first broken link; it is used by `scripts/verify_audit.py` and `GET /api/audit/verify`.
  - Activity page: a "Log intact ✓ / broken at entry N" indicator.
- **State:** migration `0003_harness_core` adds the tables `runs`, `agent_tasks`, `task_attempts` (attempt ledger `run_id:node:task_id:attempt`), `approvals`, `secret_references`, `usage_counters`, `trace_spans`, plus the audit columns. The run and task state machines live in `devteam_state.machines`. An illegal transition raises `IllegalTransition`, and the attempt is audited.

### 4.3 Execution (C.4)

- **AgentSpec**: role, kind, model slot, **operation IDs** (D-37, e.g. `brd_analyst.extract_section`), prompt id + version, input/output models, tool allowlist, write paths, budget profile.
- **AgentRole**: a closed enum of exactly 10. **AgentRegistry** accepts only `AgentRole` members, rejects duplicates, and refuses to start unless exactly 10 are registered.
- **AgentContext** gives the agent only:
  - `ctx.llm` (gateway proxy bound to its role and operations);
  - `ctx.tools` (policy-checked proxies);
  - its validated input;
  - a logger.

  It has no file handles, no database session and no settings object.
- **Agent Executor** (roadmap §5.2 lifecycle), in this order:
  1. Issue the principal.
  2. Check stage and budget.
  3. Validate the input and apply input guardrails.
  4. Run the agent.
  5. Validate the output and apply output guardrails.
  6. On invalid output, retry within the limit (F-10).
  7. Persist references, audit, usage and close the span.

  Errors are classified and mapped to `retry`, `fail`, `waiting_for_resources` or `blocked`.
- **Budget Manager**: per task and per run, across every F-9 dimension. Usage counters are persisted, and a RAM check (psutil) maps to `resource_unavailable`. A budget exhaustion stops the task cleanly and is audited.
- **Approval Service**:
  - The types are the seven in roadmap HC-13; `config/approvals.yaml` maps each type to the roles it requires.
  - Each approval is bound to its run and spec version + checksum. `verify(approval_id, run_id, spec_version, checksum)` rejects forged, mismatched or stale approvals.
  - **No API endpoints yet.** They come with each gate (I, N, P).
- **LLM Gateway**: `generate(ModelRequest) -> ModelResponse` with a global single-call lock, structured-output validation, usage and audit hooks (metadata only) and a secret check. Providers: `fake` only, which returns scripted responses in tests. Only `llm_gateway.providers` may import HTTP clients.
- **Prompt and skill registry**: interfaces only (HC-16, D-36); implementation in F. Skills grant no permissions.
- **Tracing**: OpenTelemetry spans for executor → agent → tool/model call, exported to the `trace_spans` table, reusing the Part A trace IDs.
- **Ten agent packages**: `agents/<name>/` (the canonical ten), each with `pyproject.toml` and `src/devteam_agents/<name>/spec.py` holding its `AgentSpec`. They contain no behaviour.
- **Bypass prevention made active:**
  - **Import-linter:** agents may import only `devteam_core` and `devteam_harness.agent_sdk`; the ten agents are mutually independent; only gateway providers may import `httpx`.
  - **Ruff banned-API for `agents/**`:** `os`, `subprocess`, `shutil`, `socket`, `pathlib`, `httpx`, `requests`, `ollama`, `sqlite3`, `sqlalchemy`, `importlib`.
  - **An AST test** for what import rules cannot catch: `open(`, `eval`, `exec`, `__import__`.
  - `check_agent_count.py --strict` runs in the gate.

---

## 5. Files

**Create:**
- `packages/core/src/devteam_core/contracts/{__init__,base,records,tasks,agents,tools,llm,events,statuses,errors,state}.py` and `schemas/*.json`.
- In `packages/harness/src/devteam_harness/`: `agent_sdk/`, `executor/`, `guardrails/`, `secrets/`, `tools/` (incl. `files.py`, `paths.py`), `budgets/`, `usage/`, `resilience/`, `approvals/`, `llm_gateway/{gateway.py, providers/fake.py}`, `prompts/` (interface), `observability/tracing.py`, and additions to `audit/` (chain) and `iam/` (agent and service principals).
- `packages/state/src/devteam_state/machines.py`, new repositories, and migration `0003_harness_core`.
- `agents/<10 names>/…`.
- Config: `config/guardrails/{policies,input_guardrails,output_guardrails,protected_paths}.yaml`, `config/approvals.yaml`, `config/budgets.yaml`.
- Scripts: `scripts/export_contract_schemas.py`, `scripts/verify_audit.py`, `scripts/demo_part_c.py`.
- Docs: `docs/architecture/{contracts,harness}.md`, plus `docs/parts/PART-C-COMPLETION-REPORT.md` at the end.

**Modify:**
- Root `pyproject.toml`: workspace members, import-linter contracts, ruff per-file banned-API, coverage.
- `scripts/check_agent_count.py`: strict by default.
- Settings, extended with the new config sections.
- Audit repository: chained append.
- API: `GET /api/audit/verify`; key loading via `SecretsManager` (D-18a).
- Web Activity page: indicator.
- README, CHANGELOG, trackers.

---

## 6. Dependencies to add

| Package | Constraint | Licence | Reason |
|---|---|---|---|
| `keyring` | `>=25,<26` | MIT | HC-17 (D-18) |
| `opentelemetry-api` | `>=1.27,<2` | Apache-2.0 | HC-11 (D-30); already present transitively |
| `opentelemetry-sdk` | `>=1.27,<2` | Apache-2.0 | Span processor + local SQLite exporter (D-30) |

`hypothesis` and `psutil` are already dev dependencies. No paid or networked services are involved.

---

## 7. Tests mapped to acceptance criteria

| AC | Tests |
|---|---|
| **AC-C-01** Illegal tool/path/role/stage calls rejected **and** audited | Policy matrix (every role × tool × stage). Path-attack suite: hypothesis fuzzing plus explicit cases (`..`, `%2e%2e`, `C:\`, `\\server\share`, symlink, **junction**, `::$DATA`, `CON`, case variants, trailing dot). Each denial asserts the audit row exists |
| **AC-C-02** Budgets stop execution in each dimension | One test per dimension (time, tokens, calls, retries, context, tool calls, run total) through the executor |
| **AC-C-03** All 10 agent I/O contracts exist and are exported | Round-trip tests + JSON Schema snapshots; `len(AgentRole) == 10`; registry rejects 9, 11 and duplicates |
| **AC-C-04** Agents cannot import I/O libraries, other agents or providers | import-linter contracts (run in tests); ruff banned-API; AST test; a **planted violation** in a temporary copy proves each check fails when it should |
| **AC-C-05** Secrets never in logs or audit | Canary secret through keyring → tool → gateway → error paths; logs, audit rows, spans and exception text scanned |
| **AC-C-06** Coverage ≥ 90% (harness, contracts) | `pytest --cov` with per-package thresholds |
| **AC-C-07** Spec scenario #24 | Integration: the stand-in agent attempts a traversal write and an unregistered tool. Both are rejected; the audit shows the denial with rule ID; the chain verifies |

Also covered:
- **Hash chain:** tamper detection (edit, delete and reorder a row in a copied DB); the anchor covers Part B rows.
- **State machines:** illegal transitions are rejected and audited.
- **Retry classification and backoff.**
- **Approvals:** the Working Plan §10.3 checks that apply without a graph (wrong role, forged ID, mismatched version, stale checksum, double submission).
- **Gateway:** the fake adapter, the lock, schema retry, and refusal when a secret appears in a prompt.
- **Executor integration:** allowed call succeeds and is audited; denied call is rejected and audited; invalid output retries within the bound; budget exhaustion stops the task.
- **Key migration (D-18a):** the file is imported, verified, then removed; sign-in still works.
- **Regression:** all Part A–B tests stay green. The security suite becomes a required gate.

---

## 8. Demonstration (G-C)

1. Run `uv run python scripts/demo_part_c.py`. It uses a temporary data folder and does not touch your real data. It prints in plain words:
   - the stand-in agent reads an allowed file → **allowed**;
   - it tries `..\..\Windows\win.ini` → **refused, logged**;
   - it tries a tool it doesn't have → **refused, logged**;
   - it runs out of its attempt budget → **stopped**;
   - the log check → **intact**. The script then edits one log entry in the temporary copy, and the check reports **broken at entry N**.
2. Run `uv run pytest tests/security -q` to see the security suite live.
3. Contract snapshot and the ten-agent check.
4. Activity page in the app, showing the "Log intact" indicator.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Part C is the largest part | Four checkpoints, each with its own tests, tracker note, local commit and progress-tracker entry. If a checkpoint overruns, I stop and report rather than cut tests |
| Contracts written now may need changes later | `schema_version` on every model; changes go through a version bump and snapshot update, recorded in the tracker |
| Symlink tests need Windows Developer Mode | Junction tests need no special rights and always run. Symlink tests run if allowed; otherwise they are reported as **skipped with the reason**, never counted as passed |
| Keyring behaviour differs in tests | In-memory backend for the suite; one real-keyring test run and reported separately |
| Single-writer contention on the audit chain | `BEGIN IMMEDIATE` + existing `busy_timeout`; a concurrency test with parallel writers |
| Import-linter cannot ban every built-in | Ruff banned-API plus the AST test cover the rest; planted-violation tests prove each layer bites |

---

## 10. Checkpoint breakdown

Each checkpoint follows the same procedure:
1. Write the tests first for the safety-critical modules (CLAUDE.md §9), and see them fail for the right reason.
2. Implement.
3. Run the full fast suite plus static gates, so Parts A–B stay green.
4. Write a tracker checkpoint note (CH/TR rows).
5. Add a progress-tracker paragraph.
6. Make one local commit on `part/c-harness-core`.

A checkpoint is not "done" with any failing, skipped-without-reason or placeholder test. Every security test has a **negative control**: a variant with the protection removed that must fail.

| Checkpoint | Order of work | Exit criteria (all measured, none assumed) | Commit |
|---|---|---|---|
| **C.1 Contracts** (HS-06) | 1. `statuses`, `errors`, `base` (incl. `Provenance`, `SourceRef` matching the Part B reference format). 2. 22 domain records with F-1…F-8 enums. 3. `AgentTask` (+ F-11), `TaskPlan` structural validation. 4. Input/output models for all 10 agents (Spec §6.2–§6.11). 5. `tools`, `llm`, `events`, `state` (`RunState`). 6. Schema export script + snapshot + "export up to date" test | Round-trip for every model; snapshot test; `extra` fields rejected; `TaskPlan` rejects cycles, unknown agents and missing dependencies; `brd_fact` without source rejected; `len(AgentRole)==10` | `feat(contracts): C.1 shared contracts v1` |
| **C.2 Identity, policy, secrets** (HS-07…09) | Tests first: policy matrix, path rules, canary secret. 1. Agent/service principals (agents get no approval rights). 2. Policy engine + `config/guardrails/*.yaml` with schema validation (unknown keys rejected). 3. Input/output/stage guardrails, incl. the forbidden-claims rule. 4. `SecretsManager` (keyring) with fail-closed start and no file fallback. 5. **D-18a key migration** with the four owner checks, run against your real install only at the end of C.2 after a backup of `data/` | Every role × tool × stage cell tested allow/deny; deny is audited; canary never appears in logs, exceptions, audit or spans; keyring-unavailable → API refuses to start; migration result recorded (pass/fail per check, no secret). If any migration check fails, **stop and report**, file kept | `feat(harness): C.2 IAM, policy, guardrails, secrets` |
| **C.3 Tools, audit, state** (HS-10…12) | Tests first: path-attack suite (hypothesis + explicit Windows cases), tamper detection. 1. `ToolSpec`/`ToolRegistry`. 2. Controlled File Service + the five tools in §4.2. 3. Migration `0003_harness_core` (upgrade **and** downgrade tested; automatic backup before migrating). 4. Hash chain + anchor event for Part B rows + `verify_chain()` + `GET /api/audit/verify` + Activity indicator. 5. Run/task state machines, repositories, attempt ledger | Every listed attack rejected and audited; junction escape rejected; frozen files protected; diff + checksum on each write; tamper (edit/delete/reorder) detected at the exact entry; Part B audit rows untouched and covered by the anchor; illegal transitions rejected + audited; concurrent writers keep a valid chain | `feat(harness): C.3 tools, file service, audit chain, state machines` |
| **C.4 Execution** (HS-13…16) | 1. Budget Manager + usage counters (F-9), retry policy (F-10). 2. Approval Service + `config/approvals.yaml`. 3. Gateway interface + `fake` provider + global lock + secret check. 4. Prompt/skill registry interfaces. 5. OpenTelemetry tracing with local SQLite exporter + attribute allowlist. 6. Agent SDK + Executor. 7. Ten agent packages (`AgentSpec` only). 8. Import-linter, ruff banned-API, AST test, `check_agent_count.py --strict` | Every F-9 dimension stops a task; F-10 retry counts exact; approvals reject wrong role, forged ID, mismatched version, stale checksum, double submit; no network trace exporter; stand-in agent integration test (allowed/denied/retry/budget); planted violations make each static check fail; coverage ≥ 90% on harness and contracts | `feat(harness): C.4 executor, budgets, approvals, gateway, agents` |
| **Gate G-C** | Full regression A–C; security suite; gitleaks; dependency audits; demo script; completion report; release prep | All AC-C-01…07 met with evidence | (after your approval) merge + `v0.3.0` + `part-c-approved` |

**Order dependency:** C.2 needs C.1 contracts. C.3 needs the C.2 policy engine (the tools call it). C.4 needs everything before it. If a checkpoint overruns, I stop at the checkpoint boundary and report; nothing is cut to fit.

---

## Owner confirmation: ☑ D-32 · ☑ D-18 · ☑ D-18a (with conditions) · ☑ D-30 · ☑ D-26 (AP-007, 2026-11-20) · ☑ F-1…F-11 (AP-008, F-9d = 180 s) · ☑ start implementation (AP-008)
