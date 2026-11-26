# Context Manager and project memory (Part F.5; HC-08; Spec §9.4)

## Context Manager (`devteam_harness.context`)

Assembles what one model call may see, inside the role's token budget:

1. **required** items (task input and records the step needs) first, never trimmed; if they alone exceed
   the input budget the call is refused (`budget_exhausted`), never silently cut;
2. **retrieved** chunks by score, whole chunks only, up to the role's retrieval budget;
3. **memory** notes, whole notes only, up to the role's memory budget.

Truncation drops whole items, never the inside of an item. Budgets (`context:` in `config/defaults.yaml`):

| Role group | Retrieval tokens | Memory tokens |
|---|---|---|
| Discovery (BRD Analyst, Requirements Finalizer) | 1,536 | 256 |
| Build roles | 1,024 | 512 |
| Documenter | 768 | 512 |

The input budget itself comes from `config/budgets.yaml` (3,072 tokens on the default profile). Agents
start using the Context Manager in Part H; in Part F it is the tested building block.

## Project memory (`devteam_harness.memory`)

Read-only facts from SQLite, never from the vector store: frozen specification versions (version, checksum,
requirement keys) and recorded human decisions. Each fact is short metadata text without document content,
usable as a `memory` item.

## Token counting

The conservative estimate (about 3 characters per token) stays the floor. The gateway calibrates a per-model
ratio from **cold** calls only (`prompt_eval_count` of a warm call can omit tokens reused from Ollama's cache)
and never estimates below the base estimate.
