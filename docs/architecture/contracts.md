# Shared Contracts (C.1, HS-06)

Package: `devteam_core.contracts` · Schema version: **1** · Exported schemas: `packages/core/src/devteam_core/contracts/schemas/*.v1.json` (71 files)

Every interface that agents, harness and services exchange is a Pydantic v2 model deriving from `Contract`:

- **Unknown fields are rejected** (`extra="forbid"`), so an agent cannot add a claim the contract does not define.
- **Instances are immutable.** Changes are made with `model_copy(update=...)` and the result is validated again.
- **Every model carries `schema_version`.** Changing a contract means bumping the version, re-exporting the schemas (`uv run python scripts/export_contract_schemas.py`) and recording the change in the tracker. The snapshot test fails when the files and the code disagree.

## Modules

| Module | Contents | Source |
|---|---|---|
| `_base` | `Contract`; ID types (`prj_…`, `tsk_…`, …; chunk IDs `chk_` + 26 hex); stable keys `FR-/NFR-/BR-nnn`; `RelPath`/`PathScope` with the structural path rules; `path_in_scope` | Spec §6.2, §11.3 |
| `statuses` | `AgentRole` (exactly 10), role groups, `RunStatus`, `TaskStatus` (9), `ProjectVerificationStatus` (8), `FeasibilityStatus`, `Stage`, `GateName`, `ApprovalType`, `ModelSlot` | Spec §5, §18.1, §20.2; roadmap §4.1, §6.4–6.5 |
| `errors` | `ErrorCategory`, `ErrorRef`, `is_auto_retryable` (policy and budget outcomes are never retried: F-10c) | HC-12 |
| `base` | `Provenance`, `SourceRef`, `Sourced`, `NeverAssumed` | Spec §5.4.1, §6.1–6.2 |
| `records` | 22 domain records with the F-1…F-8 values | Spec §12; PART-C-PLAN §2.1 |
| `tasks` | `AgentTask` (Spec §6.4 + F-11), `TaskPlan` | Spec §6.4 |
| `agents` | Input/output models for all 10 agents; `AGENT_IO`; `check_output_against_input` | Spec §6.2–6.11 |
| `tools` | `ToolCall`, `ToolResult`, `SideEffect`, principal formats | Spec §11.2 |
| `llm` | `ChatMessage` (trust labels), `ModelParams` (F-9 bounds), `ModelRequest`, `ModelResponse` | HC-02; D-35, D-37 |
| `events` | Metadata-only events (run/task transitions, tool and model calls, approvals, file changes) | Spec §11.4 |
| `state` | `RunState` (references only) | Roadmap §6.8 |

## Rules the contracts enforce

| Rule | Where |
|---|---|
| Paths are workspace-relative. The following are rejected: `..`, `.`, empty segments, absolute paths, drive letters, `:` (drives and NTFS streams), backslashes, `%` encoding, control characters, Windows device names, trailing dots or spaces, and more than 240 characters | `_base.check_relative_path` (applied to every path field). The Controlled File Service (C.3) repeats the check against the real filesystem |
| Requirements, rules, roles, permissions, entities, workflows, integrations, technologies and constraints come only from the BRD (`brd_fact` with a chunk citation) or from the user (`user_answer` referencing the answer) | `base.NeverAssumed` |
| A task's operation belongs to its own agent. Build, Head and Documenter tasks name the approved spec version. Expected artefacts lie inside the permitted paths. Attempts are capped at 4. A task holds only task statuses | `tasks.AgentTask` |
| Build plans contain only agents 4–9, from one project, run and spec version, and have known dependencies and no cycles | `tasks.TaskPlan` |
| No agent output has a field for test results, verification, project status or approval. Records belong to the agent's own project | `agents` output models |
| The analyst cites only chunks it was given. The finalizer asks only about code-decided critical gaps, and never in a second round. The Head plan uses the approved spec, covers the in-scope keys and routes only real failures, and never to itself. Build agents write only within the task scope. The Debugger edits tests only under an approved test-maintenance task. Mappings stay within the task's requirements. The Documenter writes only `docs/` and root docs | `agents.check_output_against_input`, called by the Agent Executor (C.4) |
| Passing or failing evidence needs an executed test run; manual evidence needs an approval. A "passed" test run cannot report failures | `records.RequirementEvidence`, `records.TestRun` |
| Approvals and spec versions are decided by a human principal (`prn_…`). Spec approvals are bound to version and checksum | `records.Approval`, `records.SpecVersion`, `events.ApprovalRecorded` |
| An agent principal (`agent:<role>:<task>`) is valid only for its own task. Tool arguments are at most 64 KiB and results at most 1 MiB | `tools` |
| System prompts are trusted and come first. Untrusted content travels only as a labelled user message. Output and context limits stay within F-9 | `llm` |
| Events carry metadata only: no prompt, output or file text | `events` |
| Build and delivery stages need a frozen spec version and checksum | `state.RunState` |

## Not yet enforced (later checkpoints)

These checkpoints repeat the contract rules at runtime against real resources:

- **C.2:** policy (role × tool × path × stage).
- **C.3:** filesystem checks (symlinks, junctions, case), and the database-side state machines and audit chain.
- **C.4:** budgets and executor cross-checks.
