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
