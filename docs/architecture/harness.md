# Harness Core (Part C)

This document grows one section per checkpoint. The contracts it uses are described in [contracts.md](contracts.md).

## C.2: Identity, policy, guardrails, secrets (HS-07, HS-08, HS-09)

### Identity (HC-15): `devteam_harness.iam`

| Principal | Form | Created by | Approval authority |
|---|---|---|---|
| Human | `prn_…` with roles `project_user`, `project_admin`, `reviewer` (Part B) | API bootstrap | Yes. Which role may approve which gate is set in C.4 (`config/approvals.yaml`) |
| Agent | `agent:<role>:<task_id>` (`AgentPrincipal`) | **Only** `issue_agent_principal(task)`, called by the executor (C.4) | **Never** |
| Service | `service:<name>` (`ServicePrincipal`, closed `ServiceName` list) | Harness components and deterministic services | **Never** |

### Policy engine (HC-01): `devteam_harness.guardrails.policy`

The engine denies by default. Each request is decided on role × tool × path × stage. The effective permission is the central policy (`config/guardrails/policies.yaml`) ∩ the AgentSpec write paths ∩ the task's `permitted_paths` ∩ the stage rule, so a task can narrow an agent's scope but never widen it.

Checks run in this order, and the first failure decides:

1. `not_an_agent`
2. `task_mismatch`: the identity must match the task's role, task, run and project
3. `default`: unknown or ungranted tool
4. `stage`
5. `spec_not_approved`: build, delivery and change-request work needs an approved frozen spec
6. `access_not_granted`
7. `path_required`
8. `path_invalid`
9. `path_protected`: `uploads/`, `extracted/`, `runs/`, `package/`; `uploads/` is not readable either
10. `path_frozen`
11. `path_out_of_scope`
12. `test_edit_requires_approval`: the Debugger may edit tests only in an approved test-maintenance task

Every decision names its rule. `PolicyEnforcer` writes an audit event `policy.denied` for each denial before returning it. That event holds the rule, tool, access, stage, a bounded and redacted path, the task and the policy digest. If the audit event cannot be written, the call fails closed.

#### Protection against policy manipulation (`guardrails/config.py`)

The YAML can only narrow permissions. Code-level ceilings taken from the specification reject:
- tools outside the Part C catalogue (there is never a shell tool);
- write scopes beyond each agent's ceiling. The Head Agent and the discovery agents never write files, and the Tester writes only under `generated/tests/`;
- any scope over a protected area;
- stages outside the agent's lifecycle;
- removal of the built-in protected areas or of the mandatory forbidden-claim checks;
- unknown keys or versions, and invalid regular expressions.

A missing or invalid file stops loading altogether (fail closed). The loaded policy is immutable and carries a SHA-256 digest of the four files.

### Input guardrails (HC-01): `guardrails/input.py`

- Untrusted content (BRD text, retrieved chunks, tool output, other agents' artefacts) is wrapped between markers that carry a random 64-bit nonce. It is sent only as a labelled `user` message with an instruction to treat it as data.
- Before any model call, `check_request` refuses an untrusted message that the guard did not wrap.
- The guard refuses oversized content and secrets: registered values plus credential shapes such as private keys, GitHub/AWS/Slack/OpenAI-style keys, JWTs, bearer values and credentials in URLs. Control characters are removed.
- Injection patterns (`config/guardrails/input_guardrails.yaml`) only **flag** content for the audit trail. The structural defences above hold even when no pattern matches.

### Output guardrails (HC-01): `guardrails/output.py`

Checks run in this order: size, secret scan, schema (the agent's output contract), input/output cross-checks, forbidden claims.

- **Forbidden claims:** "tests passed", "build succeeded", "verified", "approved", "deployed". The scan covers text the agent wrote itself. Quoted BRD facts, user answers and source quotations are excluded.
- **Error categories:** schema, cross-check and claim failures are `invalid_output`, which may be retried under F-10a. A secret in the output is `policy_denied` and is never retried (F-10c).
- **Violation messages** state the location and the rule, never the content.

### Secrets (HC-17): `devteam_harness.secrets` (D-18, AP-007)

- `SecretsManager` addresses secrets by reference (`secret://platform/<name>`), and values travel as `SecretValue`, which never prints itself. Every value is registered for redaction. Errors name the reference only and never chain the backend's exception.
- `KeyringBackend` accepts only recognised OS credential stores; on this machine that is Windows Credential Manager. A plaintext or encrypted-file keyring, the `fail` or `null` backend, a chainer or any unknown backend is refused, and the API then **refuses to start**. **There is no file fallback.**
- `MemoryBackend` is for the automated tests only. It is rejected by settings validation and by the factory outside the `test` profile.
- Logging redacts at the source, so handlers outside the platform never see secrets. The platform's formatters redact again for log records from third-party libraries.

### Install key migration (D-18a): `secrets/migration.py`, `scripts/migrate_api_token.py`

The migration runs the owner's four checks, in order:

1. The key is stored in Credential Manager.
2. Reading it back gives exactly the original (constant-time comparison).
3. The real application authenticates a request using the stored key, and refuses a wrong key.
4. The existing sign-in link is unchanged, signs in through `POST /api/session` and yields a working session.

Only if all four pass is `data/api_token` deleted, and its absence confirmed. Any failure stops the migration and keeps the file unchanged. A different key already in the store is never overwritten. The result is written to the audit trail as `secret.migrated`, with check names and outcomes only. While the plaintext file exists, the API refuses to start, and `scripts/dev.ps1` runs the migration first.

## C.3: Tools, audit chain, state (HS-10, HS-11, HS-12)

### Controlled tool execution (HC-06): `devteam_harness.tools`

**`ToolSpec`** (Spec §11.2) declares:
- name and version;
- typed input and output models;
- side-effect class: `execute` and `external` are refused until the sandbox (E) and publishing (P);
- access (read, write or none) and the path argument;
- the allowed agent roles;
- a time limit;
- whether a human approval is required;
- audit behaviour.

Tools are added in code only. `ToolRegistry.check_consistency` refuses to start if the central policy grants a tool to a role the tool does not allow, or with a different kind of access.

**Catalogue (Part C).** There is no shell tool:

| Tool | Access | Roles |
|---|---|---|
| `read_project_file` | read | build agents 4-9, Documenter |
| `list_project_files` | read | build agents 4-9, Documenter, Head |
| `write_project_file` | write | build agents 4-9, Documenter |
| `read_extracted_document` | Part B chunks, limited to the task's own project | BRD Analyst, Requirements Finalizer |
| `search_project_knowledge` | interface only; answers "not available" until Part F | BRD Analyst, Requirements Finalizer, Architect |

**`ToolLayer.invoke`** runs these steps in order and stops at the first failure:

1. Registry: unknown tool or version → `deny.unregistered_tool`.
2. Identity: the call must carry the principal and task issued by the executor → `deny.task_mismatch`.
3. Tool roles → `deny.tool_role`.
4. Argument schema → error `tool.invalid_arguments`.
5. Policy engine (C.2): unsafe paths are recorded as `deny.path_invalid`, because path arguments are plain strings precisely so the policy decides.
6. Approval requirement → `deny.approval_required`.
7. Per-task write budget, F-9j → `budget_exhausted`.
8. The handler runs under its time limit. On timeout a cancellation flag is set, so a pending write is abandoned before it is committed.
9. The file service re-checks the path on disk → `deny.path_unsafe`.

Every outcome is audited:
- denials as `policy.denied` or `tool.denied`, naming the rule;
- executions as `tool.called`, with a bounded and redacted argument summary; file contents are summarised as `<N chars>` only;
- writes also as `file.changed`, with the SHA-256 before and after, the bytes written, and a reference to the diff saved in `runs/<run_id>/diffs/<call_id>.diff`.

**Controlled File Service** (`tools/files.py`):
- The workspace root must not be a link.
- These are refused:
  - the structural path rules from the contracts;
  - 8.3 short-name aliases (`NAME~1`);
  - a junction, symlink or other reparse point on any existing component;
  - any path that resolves anywhere other than requested (compared case-insensitively);
  - reading or overwriting a hard-linked file.
- Byte limits come from `config` (`files:`, F-9i), and text must be UTF-8.
- Writes are atomic: an exclusive temporary file is flushed, re-checked and then moved into place with `replace`. A cancelled write leaves no trace.

### Hash-chained audit (HC-21): `devteam_state.audit_chain`, `repositories/audit.py`

- **Each new entry** stores `seq`, `prev_hash` and `hash = SHA-256(prev_hash || canonical entry)`.
- **Ordering:** appenders are serialised by updating the single-row `audit_chain_head` first, which takes SQLite's write lock. The unique `seq` index is a backstop.
- **Migration `0003_harness_core`** adds the chain without touching Part B rows:
  - an anchor entry (seq 1) stores their count and digest;
  - the append-only triggers stay in force throughout;
  - the head row cannot be deleted or moved backwards.
- **`verify_chain`** reports the first broken entry. It covers:
  - edited entries;
  - deleted entries, including the newest (by comparing with the head);
  - inserted or reordered entries;
  - changes to Part B rows.

  It is exposed through `GET /api/audit/verify`, the Activity page indicator ("Log intact" or "broken at entry N") and `scripts/verify_audit.py`.
- **Known limit (KI-019):** someone with direct file access could drop the triggers, delete the newest entries *and* rewind the head to match, and that can't be detected from inside the same file. Keeping a copy of the head hash outside the database (with run history in Part D) closes this gap.

### Run and task state (HC-19): `devteam_state.machines`, `repositories/runs.py`

- **Tables:** `runs`, `agent_tasks` (the validated `AgentTask` contract plus queried fields) and `task_attempts`, the attempt ledger keyed `run_id:node:task_id:attempt`, which is unique.
- **Status changes** go only through `transition_run` and `transition_task`, checked against fixed tables (roadmap §6.4, Spec §20.2). Terminal states have no exit. A failed task may become ready again only while attempts remain (F-10d).
- **Legal changes** are audited in the caller's transaction.
- **Illegal changes** raise `IllegalTransitionError` and are audited as `*.transition_denied` once the caller's transaction ends, so the record survives the rollback and never waits on SQLite's single-writer lock.
- **`start_attempt`** returns `already_completed` for a replayed attempt, so side effects are not repeated after a restart.

## C.4: Execution (HS-13, HS-14, HS-15, HS-16)

### Budgets and usage (HC-05): `devteam_harness.budgets`, `config/budgets.yaml`

- **Configuration.** The F-9 and F-10 values approved in AP-008 live in `config/budgets.yaml`. The approved min–max ranges are fixed in code, so the file can choose a value inside a range but never outside it. Loading fails closed on any of these:
  - an unknown key;
  - a missing `default` profile;
  - input + answer tokens that don't fit the context window;
  - a per-task limit larger than the whole run's;
  - any attempt to configure retries after a policy denial or exhausted budget (F-10c is fixed at 0).
- **`BudgetManager.open_task(task)`** loads the task's and the run's persisted usage (`usage_counters`), so a restart keeps counting.
- **Checks before every action:**

  | Dimension | Limit |
  |---|---|
  | answer length | F-9a |
  | input context (conservative estimate) | F-9b |
  | context window | F-9c |
  | model calls per task | F-9e |
  | tokens per task, reserving the full answer | F-9f |
  | wall-clock per task | F-9g |
  | tool calls per task | F-9h |
  | active time per run | F-9k |
  | tokens per run | F-9l |
  | model calls per run | F-9m |

- **When a limit is reached:** `BudgetExhaustedError` (`budget_exhausted`, never retried) and an audit event `budget.exhausted` naming the dimension.
- **Too little free memory (F-9n)** raises `resource_unavailable` instead. The run waits; the task is not failed.

### Retry policy (HC-12): `devteam_harness.resilience`

`RetryPolicy.decide(category, retries_done)`:

| Category | Retries | Wait |
|---|---|---|
| `invalid_output` | F-10a (2) | none; the violations are sent back |
| `retryable` | F-10b (2) | 2 s, then 4 s, up to +25% jitter, never above 30 s |
| everything else | 0 | — |

### Approval Service (HC-13): `devteam_harness.approvals`, `config/approvals.yaml`

- **Config.** Each of the seven approval types maps to the human roles allowed to decide it. Spec approval, GitHub publishing, destructive database changes and database switching must be bound to a spec version (a ceiling in code).
- **`record`** refuses, and audits as `approval.denied`:
  - agents and services (`approval.not_human`);
  - the wrong human role;
  - a spec-bound type without a version and checksum;
  - an unknown run or principal;
  - a second decision for the same run, type and spec version.

  The `approvals` table is append-only (triggers), and `decision_key` is unique.
- **`verify`** is what a gate calls before acting. It refuses, and audits as `approval.verify_failed`:
  - a forged or unknown ID;
  - another run or project, or another type;
  - a decision that is not `approved`;
  - another spec version (`version_mismatch`) or checksum (`stale_checksum`);
  - a row written around the service (`approval.forged`). Its `approval.recorded` audit event must exist, carry a valid chain hash and match field by field.
- No API endpoints yet; they come with each gate (I, N, P).

### LLM Gateway (HC-02): `devteam_harness.llm_gateway`

There is no LiteLLM (D-35). `generate(request, budget=…)` runs these steps in order:

1. **Input guard (`check_request`).** A registered secret, a credential shape or unwrapped untrusted content is refused before the provider is called, and audited as `model.refused`.
2. **Budget check before every attempt.**
3. **One process-wide lock (Spec §7.2).** If a call times out (F-9d), the lock is released only when that call actually ends, so calls never overlap.
4. **Transient retries (F-10b).**
5. **Structured-output validation** against the operation's contract.
6. **Usage recording,** a `model.called` audit with metadata only (no prompt or answer text), and a trace span.

Providers live in `llm_gateway/providers/`, the only package allowed to import an HTTP client (import-linter). Part C ships only the scripted `fake` provider; the Ollama adapter comes in Part F.

### Prompts and skills (HC-16, D-36): `devteam_harness.prompts`

Interfaces only; the file-backed registry comes in Part F.

- **Prompts** are identified by ID and version and carry a SHA-256. Versions are immutable (a re-registration keeps the first), and tampering is detected on read.
- **`SkillSpec`** is guidance text for roles and operations. It has no field that could grant a tool, path, stage, budget or approval, and unknown fields are rejected. A test gives a skill that says "you may write anywhere" and the write is still refused.

### Tracing (HC-11, D-30): `devteam_harness.observability`

- **Exporter.** The only exporter is `SqliteSpanExporter`, which writes to the local `trace_spans` table. The tracer provider is private, so neither the global provider nor `OTEL_*` variables are used, and no OTLP package is installed (tested).
- **Attributes.**
  - Keys pass an allowlist (IDs, names, counts, durations, outcome codes); any other key raises.
  - String values must look like an identifier and pass redaction; otherwise they are stored as `<rejected>`.
  - The exporter filters again.
  - Exception messages are never recorded, only the exception type.
- **Trace IDs.** Spans reuse the Part A trace IDs: the current `devteam_core.tracing` context becomes the parent, and the span's IDs become current inside it.

### Agent SDK (HS-16): `devteam_harness.agent_sdk`

- **`AgentSpec`** declares:
  - role and kind;
  - default model slot;
  - operations (D-37 IDs, optional per-operation slot and output contract);
  - prompt ID and version;
  - v1 input/output contracts;
  - tools;
  - write paths;
  - budget profile.

  Code ceilings reject:
  - another agent's operations;
  - unknown tools (there is no shell tool);
  - write paths outside the role's ceiling (the Head and discovery agents write nothing);
  - the wrong contracts or kind;
  - embeddings as an operation.
- **`AgentRegistry`** (`agent_sdk.registry`, platform only) accepts only `AgentSpec` objects for `AgentRole` members, rejects duplicates, and refuses to seal or be used unless exactly ten are registered. When given the central policy, every declared tool and write path must be granted there.
- **`AgentContext`** gives an agent only:
  - `llm.generate(operation, data)`;
  - `tools.call(tool, **args)`;
  - its validated `input`;
  - `feedback` (the previous attempt's rule violations);
  - `log`, `role` and `task_id`.

  It has no file handles, database session, settings or secrets. The access objects hold only a closure, and the agent static rules forbid unwrapping it.

### Agent Executor (roadmap §5.2): `devteam_harness.executor`

`AgentExecutor.execute(TaskRun)` runs these steps:

1. Load the task (project-scoped) and its sealed spec, and issue `agent:<role>:<task_id>`.
2. **Entry checks.** Each refusal blocks the task, is audited as `task.refused` and runs nothing:
   - the task's operation is not declared by the agent;
   - the stage is not one of the agent's stages;
   - build or delivery work has no approved frozen spec;
   - the input is not this agent's contract for this task. It is re-validated, so `model_construct` cannot skip the checks.
3. **Idempotency.**
   - A finished task is `replayed`, not rerun.
   - An attempt interrupted by a crash is closed as failed, and the task returns to `ready`.
4. **Budget and free-memory check.** Low memory does not consume an attempt.
5. **Run.** `ready → running`, then the agent runs in a span. Model calls go through the gateway and tool calls through the Tool Layer (C.3).
6. **Output guard (C.2).** Invalid output is retried within F-10a, with the violations fed back. A secret in the output is `policy_denied`.
7. **Denials can't be hidden.** Any denial during an attempt makes the attempt `policy_denied`, even if the agent caught the exception.
8. **Finish in one transaction:** the attempt ledger, the task status and a `task.executed` audit event.

Outcome mapping:

| Outcome | Task status | Disposition |
|---|---|---|
| success | `completed` | `completed` |
| `invalid_output` | `failed` | `failed` |
| `retryable` | `failed` | `retry` (the Head may re-attempt, F-10d) |
| `fatal` | `failed` | `failed` |
| `policy_denied` or `budget_exhausted` | `blocked` | `blocked` |
| `resource_unavailable` | `ready` | `waiting_for_resources` |

On the last attempt, `resource_unavailable` becomes `failed` instead, because there is no attempt left to wait for.

### Ten agent packages and bypass prevention (AC-C-03, AC-C-04)

- **`agents/<name>/`** (D-26): ten workspace packages, each with `src/devteam_agents/<name>/spec.py` holding its `AgentSpec`. They contain declarations only, no behaviour.
- **Five ten-agent layers:**
  1. the closed `AgentRole`;
  2. `AgentRegistry`;
  3. `scripts/check_agent_count.py`, now strict by default; it also requires each package's `spec.py`;
  4. the graph topology test (Part D);
  5. CLAUDE.md.
- **Isolation, three static layers.** Each is proven by a planted violation in a temporary copy (`tests/security/test_agent_static_rules.py`):
  - **import-linter:**
    - agents may import only `devteam_core` and `devteam_harness.agent_sdk`;
    - the ten agents are independent;
    - only `llm_gateway.providers` may import `httpx`.
  - **Ruff banned-API** (`agents/ruff.toml`) bans:
    - operating-system, file, process and network modules (`os`, `pathlib`, `io`, `tempfile`, `shutil`, `subprocess`, `socket`, `urllib`);
    - HTTP clients, model and database libraries (`httpx`, `requests`, `ollama`, `sqlite3`, `sqlalchemy`);
    - `importlib`, `ctypes`, `pickle` and `keyring`;
    - `devteam_state`, `devteam_services`, `devteam_api` and every harness internal.
  - **AST rules** (`scripts/check_agent_rules.py`):
    - an import allowlist;
    - no `open`, `eval`, `exec`, `compile`, `__import__`, `getattr`, `setattr`, `delattr`, `vars`, `globals`, `locals`, `breakpoint` or `input`;
    - no private or dunder attribute access;
    - no model names.

### Demonstration

`uv run python scripts/demo_part_c.py` works in a temporary folder with the fake AI. Your data is not touched. A stand-in runs under the real Backend Developer declaration, and the script shows:

1. an allowed read and model call;
2. a refused `..\..\Windows\win.ini` write;
3. a refused shell tool that the stand-in tried to hide;
4. a stop after the retry limit;
5. "log intact";
6. after one entry is edited in the temporary copy, "broken at entry N".
