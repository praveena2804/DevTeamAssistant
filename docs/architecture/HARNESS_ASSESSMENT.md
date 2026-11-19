# Agent & Harness Architecture Assessment

| Field | Value |
|---|---|
| Status | Analysis document. **Not authoritative.** The Master Specification, roadmap and Working Plan take precedence. Proposals here become binding only through the decision log in `docs/IMPLEMENTATION_TRACKER.md` and owner approval |
| Date | 2026-11-19 (assessment produced 2026-11-19; D-35/D-36 owner directions incorporated the same day) |
| Sources | Spec v2.0 §1–§27, roadmap v2.0 §1–§21, Working Plan v1.0 §1–§21, tracker |

## 1. Ten-agent architecture

**What an agent is.** Each of the ten agents is made of:

- a model slot;
- a versioned prompt;
- typed I/O;
- a tool allowlist;
- scoped context;
- deterministic validators (Spec §6).

**How agents run and communicate:**

- Agents run sequentially, one LLM call at a time.
- **They never call each other.** They exchange data only through:
  - checksummed artefacts persisted by the harness;
  - `RunState` references;
  - Head Agent dispatch records.
- Discovery uses fixed edges with no Head Agent. In Build, the Head Agent is the hub.

## 2. Per-agent matrix

**Tool keys** (Spec §11.2, Plan TS-03/05/09):

| Key | Tool | Key | Tool |
|---|---|---|---|
| R | `read_project_file` | B | `request_build` |
| L | `list_project_files` | T | `request_test_run` |
| K | `search_project_knowledge` | M | `request_migration_check` |
| W | `write_project_file` | P | `apply_patch` |

**No agent receives any of the following:** a shell, network, Git, GitHub or approval tool.

| # | Agent | Stage | Model slot | Tools | Write scope (inside `workspaces/<pid>/`) | Forbidden (enforced by code) | Agent-specific guardrails | Built in |
|---|---|---|---|---|---|---|---|---|
| 1 | BRD Analyst | Discovery | Instruct | R (extracted only), K | none; records persisted by the executor | shell, target code, DB mutation, GitHub | BRD delimited as untrusted; cited chunk IDs must exist; quotations must match chunk text; provenance required; injection flagged as Gap + audit; catalog keys restricted to the closed enum | D stub, H |
| 2 | Requirements Finalizer | Discovery | Instruct | R, K | none | deciding criticality (Policy Engine); approving (agent principals lack the permission); a 2nd clarification round (DB + API + graph); starting architecture (stage guard) | every summary item must reference a record or answer; set-difference check against structured records; catalog-approved options only | D, I |
| 3 | Head Agent | Build/Change | Instruct + code | none | plan, dispatch and status records only | target source, shell, tests, approvals, setting Verified. **Same harness, no exception** | plan validator; task `permitted_paths` ⊆ target agent's scope (can only narrow) | D core, J planner |
| 4 | Architecture Agent | Build | Instruct | R, K, W | `generated/docs/`, `generated/contracts/` | shell, GitHub, stack change (drift validator), writes after freeze | OpenAPI validation; endpoint→requirement mapping | D, J |
| 5 | Database Developer | Build | Coder | R, W, M | `generated/database/`, backend model slots | applying any migration (harness does it in the sandbox); production DBs | destructive-op detector; entity coverage; secret scan | D, K |
| 6 | Backend Developer | Build | Coder | R, W, B, T | `generated/backend/`, shared config | invented endpoints (contract diff), hardcoded secrets, fake persistence | conformance diff; Ruff/Bandit recipes | D, K |
| 7 | Frontend Developer | Build | Coder | R, W, B | `generated/frontend/` | invented routes, hard-coded data as persistence | route scanner; generated client read-only | D, L |
| 8 | Testing & Validation Agent | Verify | Coder | R, W, T | `generated/tests/` | writing results (runner-only) | requirement-marker validator; trivial-test detector | D, M |
| 9 | Debugger & Code Reviewer | Verify | Coder | R, P, T, B | non-test source named by failure evidence | `tests/` unless an approved test-maintenance task; unrelated changes | diff scope checker + test snapshot hash; logs treated as untrusted | D, N |
| 10 | Documentation & Reporting Agent | Deliver | Instruct | R, L, W | `generated/docs/`, target root `README` | invented endpoints, results, files or URLs | code-generated facts; cross-check validator | D, O |

## 3. Shared controls (every agent)

| Control | Enforced by |
|---|---|
| Task-scoped principal `agent:<role>:<task_id>` | HC-15 |
| Deny-by-default role × tool × path × stage | HC-01 |
| Banned I/O and provider imports | Ruff banned-API + import-linter (roadmap §5.3) |
| Model access only via the gateway | HC-02, HC-16 |
| Pydantic at every boundary | HC-20 |
| Forbidden-claim, secret and provenance output guardrails | HC-01 |
| Per-task and per-run budgets | HC-05 |
| Secrets never in context | HC-17 |
| Hash-chained audit | HC-21 |
| Tracing | HC-11 |
| Global single LLM / sandbox lock | P8 |

## 4. Design gaps to close in part plans

| ID | Gap | Proposed handling | Part |
|---|---|---|---|
| G-1 | Trust labels covered only BRD text | Trust level on every content item (`platform_trusted`, `human_approved`, `agent_generated`, `external_untrusted`); only platform-trusted content may sit outside data delimiters | C.1 |
| G-2 | No fail-closed rule for missing controls | Executor preflight → `ControlMissing` → audited → task BLOCKED | C.4 |
| G-3 | Permission combination undefined | Effective = AgentSpec ∩ central policy ∩ task scope ∩ stage; any AgentSpec/policy mismatch fails startup | C.2/C.4/D |
| G-4 | "Write structured records" tool undefined | Executor persists validated outputs; no DB tool for agents | C.4 |
| G-5 | Generated client writability ambiguous | Read-only to the agent; harness regenerates it | L |
| G-6 | Tool output not delimited | Delimit and label as untrusted | C/N |
| G-7 | Unsupported-claim check only for the Documenter | Extend to the Finalizer summary and Head plan | I/J |
| G-8 | `create_checkpoint` owner undefined | Service/Head-core action; given to no agent | C |

## 5. Adversarial test placement

Each test is written test-first and includes a negative control showing the attack succeeds when the protection is removed.

| # | Attack | Part | # | Attack | Part |
|---|---|---|---|---|---|
| 1 | Injection through BRD content | C, H | 13 | Made-up source references | H |
| 2 | Injection through retrieved documents | F | 14 | Fabricated test results | C, M |
| 3 | Malicious tool output | C, N | 15 | Invalid JSON / structured output | C, F |
| 4 | Malicious artefact handoff between agents | D, J–O | 16 | Cross-project memory access | B, F |
| 5 | Unauthorised file access | C | 17 | Unauthorised approval | C, D |
| 6 | Path traversal | C | 18 | Approval bypass | D, I |
| 7 | Unauthorised tool call | C | 19 | Sandbox escape | E |
| 8 | Permission escalation | C, D | 20 | Infinite agent loops | C, D, N |
| 9 | Direct model-provider access | C | 21 | Token budget exhaustion | C, F |
| 10 | Gateway bypass | C, F | 22 | Retry exhaustion | C, F, N |
| 11 | Secret extraction | C, F | 23 | Worker crash and recovery | D |
| 12 | Secrets leaking into logs | **A** (implemented: canary + negative control), C | 24 | Injected text changing harness policy | C, R |

All 24 are re-run with the full corpus in Part R.

## 6. Decisions arising

| ID | Decision | Status |
|---|---|---|
| D-35 | LiteLLM in the gateway | **Owner direction 2026-11-19:** keep the roadmap design (the gateway calls Ollama directly). Do not introduce LiteLLM without an impact assessment and owner approval |
| D-36 | Skills registry | **Owner direction 2026-11-19:** versioned skills inside the prompt/skill registry (HC-16; interface in C, implementation in F). Skills grant no permissions. Roadmap §5.1/§12 amendment **proposed, not applied** (awaits roadmap approval) |
