# Part D — Completion Report

LangGraph Orchestration, Ten Agents & Head Agent Deterministic Core · Gate **G-D** · Date: 2026-11-23

| Field | Value |
|---|---|
| Status | **Implemented, awaiting the owner's review (G-D).** Not approved, not merged, not tagged, not pushed |
| Branch | `part/d-orchestration` (local) |
| Commits | `ce49ce3` D.1 · `bf21f36` D.2 (Head core, service steps) · `4fd4a3f` D.2 completion (Ollama, ten agents) · `ab5eea0` D.3 · `17d0456` D.4 · D.5 + documentation: the commit that adds this report |
| Rulings applied | AP-015 (plan; D-27 (b), D-38…D-42, D-43 deferred), AP-016 (D.1 approved; D-38 revised: real AI only; KI-023 orjson accepted conditionally), AP-017 (D-44 (a) Ollama brought forward; KI-026 certifi accepted conditionally; fake provider kept for Part C tests only; D.2–D.5 in one run) |

## 1. What Part D delivers

1. **The workflow.** One LangGraph graph, `project_run`, with 37 nodes in four groups (discovery, build, delivery, change request). Its edges are generated from the static transition table, and a test proves that the compiled edges equal the table.
2. **The ten agents, running on the local AI.**
   - Every agent step goes through the Agent Executor and the LLM Gateway to the local Ollama.
   - Each call is constrained by an answer contract (JSON Schema), validated, audited and traced.
   - The behaviour is deliberately minimal:
     - the BRD Analyst extracts cited requirements and the completeness checklist;
     - the build agents write design notes, not code;
     - the Documenter writes run notes from recorded facts only.
   - Real behaviour arrives in Parts H–O.
3. **The Head Agent's rules are code.** A template planner, a plan validator (every problem reported with its own code), a scheduler, a dispatcher and a failure router.
4. **Building cannot start without approval (three layers).**
   1. Topology: every path into Build passes the spec freeze and the build-entry guard.
   2. The guard re-reads the spec version and approval from SQLite, recomputes the checksum and re-verifies the approval.
   3. Every Build or Delivery node repeats that check, so a forced jump is refused and audited.
5. **The worker.** A separate process (`python -m devteam_worker`), started by `scripts/dev.ps1`.
   - One worker and one run at a time.
   - Heartbeat, with metrics.
   - Recovery after a crash or kill, with no finished step repeated (D-40).
   - Pause, resume and cancel at step boundaries.
   - Waits for resources when Ollama or memory is unavailable.
6. **Run control and progress.**
   - API: start, read, pause, resume, cancel, gate decisions (through the Approval Service), tasks, events (JSON and live server-sent events), trace, history and metrics.
   - UI: runs on the project page, a live Build Progress page with decision buttons, a trace viewer and run history.
7. **Honesty.**
   - Services that arrive later (integration build, test runner, completion gates, packager, deployer) are stand-ins that can only report "not run / not evaluated / not built / not published".
   - The final report always says *not verified*.
   - No test result or project verification status is ever written.

## 2. Ollama integration (D-44)

| Item | Value |
|---|---|
| Provider | `devteam_harness.llm_gateway.providers.ollama.OllamaProvider`: direct HTTP to Ollama's `/api/chat`; health uses `/api/tags` and `/api/version`. No LiteLLM, no other provider |
| Local only | Loopback URL required (checked at config load and again in the provider); credentials in the URL refused; **proxy variables ignored** (`trust_env=False`); redirects not followed |
| Models (`config/defaults.yaml`, `ollama.models`) | `instruct` **qwen2.5:3b**, `coder` **qwen2.5-coder:3b**, `review` qwen2.5:3b, `fallback` qwen2.5:1.5b. These follow the Part A benchmark recommendation; the model choice is still yours to approve |
| Settings | `keep_alive` 1m; limits from `config/budgets.yaml` (1,024 output tokens, 4,096 context, 180 s per call, 8 calls per task) |
| Answers | Each operation names an answer contract (`contracts/drafts.py`), sent as Ollama's `format`. The gateway validates the answer; an invalid answer is retried with feedback (F-10a) and never patched up |
| Failures | Ollama unreachable or a model missing → *waiting for resources* (the run waits; nothing is invented). Timeout or 5xx → bounded retries (F-10b). Anything else → the task fails with its reason |
| Never | Models are never downloaded; content never leaves the machine; prompts and answers are never written to logs, audit, events or traces |
| Fake provider | Kept only for Part C's tests and `scripts/demo_part_c.py`. An import-linter contract forbids orchestration, the worker, the API and agents from importing it (planted-violation tests), and subprocess checks confirm it is never loaded |

## 3. How the ten agents execute

1. The graph node finds or creates the step's task exactly once (two-phase effects ledger).
2. The **Agent Executor** checks:
   - the operation, the stage and the approved spec;
   - the budget and free memory.
3. It then starts an attempt (attempt ledger).
4. The agent receives only an `AgentContext`:
   - `ctx.llm.generate(operation, data)` goes through the gateway: input guard (secrets refused, data delimited and labelled), budget, one call at a time, Ollama, schema validation, audit `model.called`, trace span;
   - `ctx.tools.call(...)` goes through the Tool Layer: policy, write scope, audit, file checksum.
5. The output guard checks the result (schema, cross-checks, forbidden claims, secrets).
6. Post-task verification re-reads every declared file, checks its checksum and scans it for forbidden claims.
7. The validated output is stored in `agent_outputs`; the graph state keeps only IDs.

| Agent | Operation used in Part D | What it produces |
|---|---|---|
| BRD Analyst | `analyse_section` | 13-item completeness checklist and up to 10 requirements, each citing a BRD piece (uncited items are dropped) |
| Requirements Finalizer | `consolidate` | Scope decision; exclusions accepted only after user answers (none in Part D) |
| Head Agent | `plan` | Progress summary. The plan itself comes from the template planner and validator |
| Architect, Database, Backend, Frontend Developers | `design` / `generate_code` | One design-notes document each, inside the agent's write scope |
| Tester | `generate_tests` | Test plan (never results) |
| Debugger & Code Reviewer | `repair` | Root cause and repair notes. Reached only when a repair is routed; tested directly with the real model |
| Documenter | `write_section` | Run notes from recorded facts only |

## 4. Tests and evidence

| Suite | Result |
|---|---|
| Fast suite `-m "not llm and not docker and not slow and not acceptance"` | **2,163 passed**, 1 skipped (symlink privilege, KI-020) |
| Security suite `-m security` | **1,445 passed**, 1 skipped (same) |
| Real local model (`llm`), run on this laptop | Provider: 2 passed. Runtime: Debugger repair passed. Graph: full run completes unverified, and a restarted process resumes without repeating work, both passed (12 min). Worker kill and restart: passed (5.4 min). E2E-2 through API + worker + loopback-only jail: **passed** (after fixing three test-side problems and a laptop-sleep interruption, TR-0106) |
| Web | lint, typecheck, **19 tests passed**, build OK, npm audit 0 vulnerabilities |
| Static | ruff, format, mypy (strict), 12 import contracts kept, agent count and AST rules, 92 contract schemas current, OpenAPI and TypeScript types in sync |
| Coverage | **94%** overall in the complete run (all tests, real model included: 2,172 passed, 1 skipped, 0 failed, 31 min). Part D modules 74–99%. `devteam_worker/main.py` shows 0% because it runs only in subprocesses (refusal, kill/restart and E2E tests). The Ollama provider is 87%, under the 90% harness target: the uncovered lines are HTTP error branches that cannot be produced without a mock server (D-38) |

**Acceptance criteria:**

| AC | Evidence |
|---|---|
| AC-D-01 fake/stub E2E completes | Fulfilled **with the real model** (stronger than planned): in-process full run (`test_graph.py`) and E2E-2 through the API and a separate worker process. Status completed, all nine agent steps completed, run summary `verified: false`, no verification status written, SSE events in order without gaps |
| AC-D-02 kill/restart, nothing duplicated | `test_worker_kill.py`: killed during the Database Developer's model call. Attempt `interrupted` and given back; every other task attempted once; one plan, one spec freeze, one summary; audit chain intact. Also tested without a model: lease takeover, recovery requeue, D-40 (block after more than 3 consecutive crashes) |
| AC-D-03 Build unreachable without approval | Topology on the compiled edges (no path avoiding freeze or guard). Runtime, each refused and audited: forced `Command(goto=architect)`, `update_state` past the guard, no frozen spec, checksum on the run altered, spec content altered, spec of another run, forged approval on the spec |
| AC-D-04 bad plans rejected | Validator tests (every code), hypothesis property test, and the graph test: a cyclic plan → `plan.rejected` audited → run blocked |
| AC-D-05 exactly ten agent nodes | Compiled-graph metadata test; an eleventh agent node is detected (negative control) |
| AC-D-06 scenarios #7, #10, #23 | #7 = AC-D-03 runtime cases; #10 = plan rejection; #23 = kill/restart and in-process resume |

## 5. Database

| Check | Result |
|---|---|
| Migrations | `0005_orchestration` (D.1) and `0006_agent_outputs` (D.2). Each upgrade and downgrade is tested; `agent_outputs` is append-only by trigger |
| Rehearsal on a **copy** of your real `data/state.db` | Read-only copy via SQLite backup. 0004 → 0006 → 0004 → 0006: row counts unchanged (8 audit entries), audit chain intact at each step. The real file was byte-for-byte unchanged |
| Your real database | **Still at 0004.** The API backs it up and migrates it automatically the next time you start `scripts/dev.ps1` (Part A behaviour) |
| Checkpoints | `data/checkpoints.db`, separate from state, WAL, no pickle (JSON-only serializer). The canary scan found no document text in checkpoints |

## 6. Licences

- No new third-party package since D.1. `pip-audit`: no known vulnerabilities (runtime and dev).
- `orjson` 3.13.0 and `certifi` 2026.10.23 (MPL-2.0) are accepted conditionally (KI-023, KI-026).
  - Exact versions, licence expressions and notice files are verified by `tests/contract/test_third_party_licences.py`.
  - The obligations are documented in `docs/THIRD_PARTY_LICENSES.md`.
  - Neither package's source is modified or vendored.
- No GPL-family licence is installed.

## 7. Known issues and risks

Full rows are in tracker §9.

| ID | Issue |
|---|---|
| KI-027 | The Debugger is reached only when a repair is routed. In Part D the stand-in test runner never fails, so normal runs do not exercise it (tested directly with the real model) |
| KI-028 | The clarification form and change approval are compiled but fail closed: no critical-gap producer exists before Part I, and change requests come in Part Q |
| KI-029 | D-40 gives back a crashed attempt only up to the contract cap of 4 attempts. A task with 2 attempts can absorb 2 crashes "for free"; a third consumes an attempt |
| KI-030 | **One compiled graph with four node groups, not four LangGraph subgraph objects.** Chosen so the compiled edges equal the approved transition table (cancels end the run directly across groups). Please confirm this reading of ADR-0001 |
| KI-031 | The analyst reads the first 12 BRD pieces (one bounded batch); Part H analyses all of them |
| KI-032 | Design notes come from a 3B local model: useful but not authoritative. They are labelled "not built, tested or verified" and scanned for forbidden claims |
| KI-033 | Pre-existing Part C flaky test (`test_audit_chain` tamper case, 1 in 16): not changed; your decision |
| KI-034 | Your real `data/state.db` is still at 0004; migrated (with backup) at the next app start |
| KI-035 | Long real-model runs pause when the laptop goes to sleep (seen once; they continue on wake) |
| KI-024 | Network-capable transitive packages (`langsmith`, `httpx2`, …) are monitored. The E2E run inside the loopback-only jail made no outbound connection |

## 8. Parts A–C remain intact

Their behaviour is unchanged. Approved-part files touched, each minimal and recorded (DV rows):

- the head-revision expectation in two tests;
- one scripted answer in a Part C executor test, aligned with the answer contracts its agent now declares;
- the Part B route-inventory security test, extended with the new run routes (its purpose);
- `devteam_core` settings (Ollama models), events and contracts (additive), the provider package docstring, and API `main.py` router registration.

Every Part A–C test passes.
