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
