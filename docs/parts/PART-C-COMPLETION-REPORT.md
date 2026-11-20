# Part C — Shared Contracts & Harness Core — Completion Report

Roadmap ref: §13 Part C · Milestone: M1 · Gate: **G-C** · Date: 2026-11-20 · Authorisation: AP-007, AP-008 (C.1), AP-009 (C.2), AP-010 (C.3), AP-011 (C.4) · Branch: `part/c-harness-core` (local, not pushed: KI-014)

## Summary

Part C builds the safety system every agent must go through, before any agent has behaviour. It was delivered in four checkpoints under one gate (D-32 (a)).

| Checkpoint | Commit | What |
|---|---|---|
| C.1 Contracts (HS-06) | `d25e4b6` | 71 versioned shared contracts: 22 domain records, `AgentTask`/`TaskPlan`, input/output for all ten agents, tool, model-call, event and run-state shapes. JSON Schema snapshots |
| C.2 Identity, policy, secrets (HS-07…09) | `9787b3a` | Agent and service principals. Deny-by-default policy engine with code ceilings. Input/output guardrails. Windows Credential Manager secrets (no file fallback). The real sign-in key migrated under the four owner checks (D-18a) |
| C.3 Tools, audit, state (HS-10…12) | `934b7f0` | Five controlled tools (no shell). Windows-aware Controlled File Service. Hash-chained, tamper-evident audit log (Part B rows covered unchanged). Run/task state machines with attempt ledger |
| C.4 Execution (HS-13…16) | `5a5718f` | Budgets (F-9) and retries (F-10). Approval Service. LLM Gateway with fake provider. Prompt/skill interfaces. Local-only tracing. Agent SDK and Executor. The ten agent packages (declarations only). Static agent-isolation checks |

**Agents:** the ten packages exist with their `AgentSpec` declarations only. No behaviour (roadmap §13 C).

**Model calls:** none to a real model. The gateway is exercised with the scripted fake provider; the Ollama adapter is Part F.

## Acceptance criteria

| AC | Result | Evidence |
|---|---|---|
| AC-C-01 Illegal tool/path/role/stage calls rejected **and** audited | **Pass** | Policy matrix over every role × tool × stage. Path-attack suite: hypothesis fuzzing plus explicit `..`, `%2e%2e`, `C:\`, UNC, real **junction**, hard link, 8.3 alias, `::$DATA`, `CON`, case variants, trailing dot. Each denial asserts its audit row (`test_policy_engine.py`, `test_file_service.py`, `test_tool_layer.py`). Executor entry refusals (stage, spec approval, undeclared operation, wrong input) are audited as `task.refused` (`test_executor.py`). Symlink variant skipped on this account (KI-020) |
| AC-C-02 Budgets stop execution in each dimension | **Pass** | Through the executor: model calls (F-9e), task tokens (F-9f), input context (F-9b), tool calls (F-9h), wall clock (F-9g), run model calls (F-9m), run active time (F-9k), low memory (F-9n). Run tokens (F-9l), answer length (F-9a) and context window (F-9c) at Budget Manager level. Retry counts exact; F-10c never retried (`test_executor.py`, `test_budgets.py`) |
| AC-C-03 All 10 agent I/O contracts exist and are exported | **Pass** | Round-trip and snapshot tests; `len(AgentRole) == 10`; 71 schemas, 0 stale. Registry refuses 9, duplicates and post-seal additions. The ten real specs seal against the real policy (`test_agent_sdk.py`) |
| AC-C-04 Agents cannot import I/O libraries, other agents or providers | **Pass** | Import-linter (3 agent contracts), Ruff banned-API (`agents/ruff.toml`) and AST rules (`scripts/check_agent_rules.py`) are clean on the real agents. **Planted violations** in temporary copies make each layer fail: 20 AST cases, 8 Ruff bans, 4 import-linter cases (`test_agent_static_rules.py`) |
| AC-C-05 Secrets never in logs or audit | **Pass** | Canary secrets through keyring, logs (including foreign handlers), tool arguments, model prompts (refused before the provider), agent output (blocked, not retried), exception text and trace attributes. Each test scans logs, audit rows, spans and exception text |
| AC-C-06 Coverage ≥ 90% (harness, contracts) | **Pass** | Harness **96.6%**, contracts **96.2%** (branch coverage, full fast suite) |
| AC-C-07 Spec scenario #24 | **Pass** | Stand-in agent under the real Backend Developer spec attempts a traversal write and an unregistered/undeclared tool. Both are rejected; the audit shows the denial with its rule ID; the chain verifies (`test_executor.py`, `scripts/demo_part_c.py`) |

## Tests executed (actual, G-C run 2026-11-20 15:51–15:58)

| Suite | Command | Result |
|---|---|---|
| Python fast suite (Parts A–C) | `uv sync --locked`; `uv run pytest -m "not llm and not docker and not slow and not acceptance" --cov` | **1,755 passed, 0 failed, 1 skipped**. Skip: symlink creation not permitted for this Windows account (KI-020) |
| Security marker | `uv run pytest -m security` | **1,346 passed, 1 skipped** (same skip) |
| Coverage | full fast suite | **96%** total. Harness 96.6%, contracts 96.2%, core 96.2%, state 97.0%, API 94.7%, services 90.3%, agents 100% |
| Static | `ruff check`; `ruff format --check`; `mypy packages apps scripts agents` (strict, 152 files); `lint-imports`; `check_agent_count.py --strict`; `check_agent_rules.py`; `export_contract_schemas.py --check` | All clean. Import contracts **7 kept, 0 broken**; 71 schemas, 0 stale, 0 orphaned |
| Web | `npm run check:api / lint / typecheck / test / build` (PowerShell, KI-012) | Clean; Vitest **15 passed**; build OK |
| Dependency audit | `uvx pip-audit --strict` on the runtime export, and again including dev dependencies (79 packages); `npm audit --audit-level=high` | **No known vulnerabilities**; npm **0 vulnerabilities** |
| Secret scan | `gitleaks detect` (full Git history, includes all four C commits) | **No leaks** (exit 0) |
| Mutation negative controls | C.1–C.4 scratchpad scripts, one protection disabled at a time | C.1 **8/8** (TR-0041); C.2 **19/19** (TR-0053); C.3 **23/23** (TR-0065); C.4 **62/62** (TR-0074/0075; 61/62 first: the run-wide token limit had no test, added). **112 protections, all detected** |
| Demonstration | `uv run python scripts/demo_part_c.py` | Exit 0; output below |

**Not run:** there are no `docker`, `llm`, `slow` or `acceptance` tests for Part C. The real API smoke (`dev.ps1 -Smoke`) ran at C.4 (TR-0077). For G-C the real database was checked read-only, so no new log entries were written.

**Failures during Part C development, all fixed and recorded (TR-0040…TR-0077):**

| Kind | What happened |
|---|---|
| Product bugs | Invalid secret reference misreported as "store unavailable" (C.2). Two places could pass unredacted text into an error (C.2). Foreign log handlers saw unredacted events (C.2, now redacted at the source). Denial audit stalled on SQLite's write lock (C.3). Traversal logged as "invalid arguments" instead of a policy denial (C.3). Approval verification failed on SQLite's timezone-less timestamps (C.4). Identical prompt re-registration replaced the stored copy and hid tampering (C.4) |
| Test bugs | Line-ending, expectation, encoding and collection mistakes in new tests; one Part B test that piped server output into an unread pipe (KI-021, closed) |
| Coverage gap | Run-wide token limit (F-9l) had no test (found by mutation, added) |

## Real database and install (verified read-only at G-C)

| Check | Result |
|---|---|
| Schema revision | `0004_execution` (migrated 0002→0003 at C.3 and 0003→0004 at C.4, each after an automatic backup: `data/backups/20261120T084511114541Z`, `…T094410472263Z`) |
| Audit integrity | `scripts/verify_audit.py`: **intact**: 3 chained entries plus 5 Part B entries covered by the anchor |
| Earlier rows unchanged | Fingerprint of the 7 rows existing before C.4: `ec526025029f6f7d`, identical before and after the migration. The 5 Part B rows were identical across C.3 (`c63fee10f2255e06`) |
| Sign-in key (D-18a) | Present in Windows Credential Manager (`WinVaultKeyring`, service `devteam-ai`). The value was checked for presence and length only, never printed. `data/api_token` is **absent** |

## Git state

- **Branch:** `part/c-harness-core`, 4 commits ahead of `main` (`98cb991`, v0.2.0): `d25e4b6`, `9787b3a`, `934b7f0`, `5a5718f`. The G-C documentation commit follows this report.
- **Working tree:** clean, apart from the untracked spec `.docx`, which is deliberately not committed (D-34).
- **Remote:** `origin` = `praveena2804/DevTeamAssistant`. **Nothing pushed**: GitHub refuses the signed-in account `Praveena-ROR` (HTTP 403, KI-014). No force-push and no rewritten history.
- **Version:** still `0.2.0`. `v0.3.0` and `part-c-approved` are created only after your approval (merge to `main` locally).

## Security controls in this part

| Area | Controls |
|---|---|
| Contracts (C.1) | `extra="forbid"`, frozen, versioned. Agents cannot express test results, verification or approval. Workspace-relative paths only. Provenance required for BRD facts |
| Identity (HC-15) | `agent:<role>:<task>` issued only by the executor and bound to one task. Agents and services have no approval authority |
| Policy (HC-01) | Deny by default; policy ∩ AgentSpec ∩ task ∩ stage. YAML can only narrow (code ceilings). Every denial is audited, failing closed |
| Guardrails (HC-01) | Untrusted content wrapped with a random nonce and labelled. Secrets refused before any model call. Injection patterns flagged. Output: size, secrets, schema, cross-checks, forbidden claims |
| Secrets (HC-17) | Windows Credential Manager only; insecure backends refused; no plaintext fallback; the API refuses to start otherwise. Values redacted at the log source |
| Tools and files (HC-06) | Five tools, no shell. Nine ordered checks. On-disk path safety (reparse points, 8.3 aliases, hard links, resolved-path equality). Atomic writes, checksums, diffs |
| Audit (HC-21) | SHA-256 hash chain with an anchor over Part B rows, append-only triggers, head row. Tampering reported at the exact entry (UI badge, API, script) |
| State (HC-19) | Fixed transition tables. Illegal changes refused and audited. Idempotent attempt ledger |
| Budgets and retries (HC-05, HC-12) | Every F-9 limit checked before the action; usage persisted; exhaustion audited and never retried. Low memory waits instead of failing |
| Approvals (HC-13) | Human roles from `config/approvals.yaml`; bound to run and spec version + checksum; append-only. Forged, mismatched, stale, duplicate and out-of-band approvals refused and audited |
| LLM Gateway (HC-02) | No LiteLLM. Input guard before the provider. One call at a time (the lock is held past a timeout until the call really ends). Budget checked per attempt. Metadata-only audit. Only `providers/` may import HTTP clients |
| Tracing (HC-11, D-30) | Local SQLite exporter only, no network exporter installed. Attribute key allowlist plus value check and redaction. No exception messages |
| Agent isolation | Restricted `AgentContext`. A denial cannot be hidden by catching it. Import-linter, Ruff banned-API and AST rules. Ten-agent check strict by default |

## Files created / modified

Exact register: tracker CH-0045…CH-0083 (CH-0044/0045 are the plan updates).

| Location | What |
|---|---|
| `packages/core/src/devteam_core/contracts/` | 11 contract modules + 71 JSON Schemas (C.1) |
| `packages/harness/src/devteam_harness/` | `iam/` (agent/service principals, key loading), `guardrails/`, `secrets/` (C.2); `tools/`, audit sink (C.3); `budgets/`, `resilience/`, `approvals/`, `llm_gateway/`, `prompts/`, `observability/`, `agent_sdk/`, `executor/`, `errors.py` (C.4) |
| `packages/state/` | Audit chain, `machines.py`, `runs.py`, `execution.py`; migrations `0003_harness_core`, `0004_execution` |
| `agents/` | Ten packages with `AgentSpec`; `agents/ruff.toml` |
| `config/` | `guardrails/*.yaml`, `budgets.yaml`, `approvals.yaml`, `secrets:` and `files:` sections |
| `apps/api`, `apps/web` | Key loading via the keyring; `GET /api/audit/verify`; Activity-page "Log intact" badge |
| `scripts/` | `export_contract_schemas.py`, `migrate_api_token.py`, `verify_audit.py`, `check_agent_rules.py`, `demo_part_c.py`; `check_agent_count.py` (strict); `doctor.py`, `dev.ps1` |
| Tests | Contract, security (policy, secrets, guardrails, file service, tool layer, audit chain, executor, budgets, approvals, gateway, tracing, agent SDK, static rules), state and integration suites |
| Docs | `docs/architecture/contracts.md`, `docs/architecture/harness.md`, README, CHANGELOG, CLAUDE.md §9 (agent-rules gate), trackers |

## Dependencies added

| Package | Version | Licence | Reason |
|---|---|---|---|
| keyring | 25.7.0 | MIT | HC-17 (D-18) |
| opentelemetry-api / -sdk (+ semantic-conventions) | 1.45.2 / 0.66b2 | Apache-2.0 | HC-11 (D-30), local exporter only |
| psutil (runtime, was dev) | 7.2.2 | BSD-3 | F-9n free-memory check (DV-011) |
| httpx (runtime for the API, was dev) | 0.28.1 | BSD-3 | D-18a sign-in checks (DV-008) |

No web dependencies were added.

## Deviations from plan

| ID | Deviation |
|---|---|
| DV-007 | Only the state database (not all of `data/`) was backed up before the key migration |
| DV-008 | `httpx` became a runtime dependency of `apps/api` |
| DV-009 | Tool-call contract size ceilings raised to fit the approved F-9i range |
| DV-010 | The approvals/usage/trace/secret-reference tables went into C.4's own migration `0004`, not `0003` |
| DV-011 | `psutil` became a runtime dependency of the harness |

**Minor, within the plan's intent:**

- No separate `tools/paths.py`: structural path rules live in the contracts and on-disk checks in `tools/files.py`.
- Usage counters are part of `budgets/`, not a separate `usage/` package.
- The executor's per-attempt retry and outcome mapping are documented in `harness.md`.

## Known issues & limitations

| ID | Issue |
|---|---|
| KI-019 | Someone with direct database-file access could delete the newest log entries *and* rewind the head, and that can't be detected from inside the file. An external copy of the head hash (Part D) closes it |
| KI-020 | The symlink attack test cannot run on this Windows account. Junctions (same risk, no privilege) are tested and refused |
| KI-014 | Nothing pushed: GitHub refuses the signed-in account |
| KI-016 | The real-keyring test would fail on a Linux CI runner; exclude the `keyring` marker when CI is set up |
| — | Agents have no behaviour yet; the executor and gateway are proven with a stand-in and the fake AI only |
| — | The input-size check (F-9b) uses a conservative estimate of ~3 characters per token until Part F adds real token counting |
| — | Approval UI and endpoints arrive with gates in Parts I, N and P |

Earlier open items unrelated to Part C (KI-004…KI-013) are unchanged.

## Decisions

| Status | Decisions |
|---|---|
| Applied | D-18, D-18a, D-26, D-30, D-32 (a), D-35, D-36, D-37 (operation IDs in `AgentSpec`); F-1…F-11 (AP-008, F-9d = 180 s) |
| Still open (asked when needed) | D-07 (Docker, needed for Part E); model selection; D-21; D-31 final; D-33/D-34 |

## Demonstration (G-C)

1. `uv run python scripts/demo_part_c.py` (temporary folder, fake AI, your data untouched):

   ```text
   1. Read an allowed file and ask the (fake) AI              -> ALLOWED  (completed)
   2. Write to ..\..\Windows\win.ini                          -> REFUSED, LOGGED  (deny.path_invalid)
   3. Use a tool it does not have (a shell), and hide it      -> REFUSED, LOGGED  (deny.tool_not_declared)
   4. Keep giving answers in the wrong form                   -> STOPPED  (after 3 attempts: failed)
   5. Check the activity log                                  -> INTACT  (24 entries, 4 refusals recorded)
   6. Edit log entry 10 in the temporary copy, check again    -> BROKEN  (at entry 10)
   ```

2. `uv run pytest tests/security -q` shows the security suite live.
3. `uv run python scripts/check_agent_count.py` and `uv run python scripts/export_contract_schemas.py --check` show the ten-agent and contract checks.
4. `./scripts/dev.ps1`, then Activity: the "Log intact" badge.
5. `uv run python scripts/verify_audit.py` checks your real activity log.

## Approval requested: G-C

Approve gate G-C, or request changes.

On approval, these steps follow, all local; no push without a working GitHub account:

1. Bump the version to `0.3.0`.
2. Move the CHANGELOG entry from Unreleased to 0.3.0.
3. Merge `part/c-harness-core` into `main`.
4. Tag `v0.3.0` and `part-c-approved`.

Part D is not started.
