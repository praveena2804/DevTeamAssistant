# LLM Gateway, model routing and records (Part F; HC-02/03/04/05/16/18)

Every model call of the platform — agent generations, embeddings for retrieval, and evaluation runs — goes
through `devteam_harness.llm_gateway.LLMGateway`. Import contracts make any other path fail the build
(AC-F-01): only the composition roots (`orchestration.runtime`, the worker, owner scripts) construct the
Ollama provider; `rag`, `context`, `memory` and `evaluation` never import a provider or an HTTP client; the
API imports neither the gateway nor the vector store (D-57).

## One call, step by step

| Step | What happens | Where |
|---|---|---|
| 1 | Scope: the request belongs to the budgeted task; a platform service carries no task; agents never use a service principal | `gateway.generate` |
| 2 | Input guard: registered secrets and unwrapped untrusted text are refused (`model.refused`) | `guardrails.input` |
| 3 | Rate limit per agent role or service (`config/rate_limits.yaml`): wait within `max_wait_s`, else `rate.limited` → waiting for resources | `llm_gateway.limits` |
| 4 | Routing: operation > agent > slot > global (`config/models.yaml`); only approved, benchmarked models; the rule is recorded | `llm_gateway.router` |
| 5 | Response cache (D-28, **off by default**): a hit is re-validated against the answer contract; project-scoped | `llm_gateway.cache` |
| 6 | Per rung: circuit breaker, free-RAM check (`peak_ram_mb` + `ram_margin_mb`), budget, then the call under the cross-process lease (D-46) and the in-process lock; any other loaded model is unloaded first (`/api/ps` verified) | `gateway._attempt` |
| 7 | One effective timeout = min(model `timeouts.total_s`, budget call timeout, provider timeout). A timed-out call is **cancelled** (connection closed, model unloaded) before the next rung | `gateway._call_once/_cancel` |
| 8 | Failures: transient → retried on the same rung; resource (OOM, load failure, timeout, RAM, open breaker) → next rung (reduced context, then smaller models); **invalid output never steps down**; ladder exhausted → `model.exhausted` → the task waits for resources and the run checkpoints (Spec scenario #22) | `llm_gateway.resilience` |
| 9 | Validation, usage, audit `model.called`, a `ModelCall` row (no prompt, input or answer text), and a span | `repositories/model_calls.py` |

## `config/models.yaml`

Catalogue (approved, `benchmark_ref`, `peak_ram_mb`, `num_ctx`, `reduced_num_ctx`, timeouts, `slow_after_s`),
slots (`instruct`, `coder`, `review` → inherits `coder`, `coder_large` → inherits `coder`, `fallback`,
`embedding`), fallback ladders, per-agent (D-06) and per-operation (D-37) overrides — both empty by
default. Start-up rules (MODEL_ROUTING §3.3) stop the platform on: an undeclared model, an unknown agent or
operation, a slot without a model, an inheritance cycle, an override naming both or neither of slot/model,
a duplicate key, an unapproved or unbenchmarked model, a model larger than the profile's
`max_model_ram_mb`, a ladder step to a larger model, an embedding model in a chat slot or the reverse.

The old `ollama.models` setting is gone: an `ollama.models` key is rejected as unknown, so there is one
source of truth. `ollama.keep_alive` is `0s` on `low_memory` (D-59).

## Records

`model_calls` (migration 0008, append-only): project/run/task, agent **or** service, kind (generate/embed),
operation, slot, model tag and digest (from `/api/tags`), route rule, recovery step, prompt ID/version/hash,
input hash, answer contract, cache hit, tokens, duration, free RAM, outcome, error code, attempt.
`project_id` is NULL only for platform service calls without a project (evaluation). The trace page lists a
run's model calls; nothing in a record or span contains document text, prompts or answers.

## Prompts and skills (HC-16)

`prompts/<agent>/<prompt_id>.v<N>.md` (front matter + body; body hash = SHA-256 of its exact bytes, LF
only). `config/prompts.yaml` records the active version and every released hash; an edited, missing,
unlisted or mismatched file, or an agent pinned to a non-active version, stops start-up. Skills
(`prompts/skills/`) are guidance text only and grant no permission (D-36). The ten Part D prompts moved
verbatim (D-58); their hashes are unchanged.

## Evaluation (HC-09)

`scripts/run_evals.py` runs a dataset through the gateway as `service:evaluator`; scorecards go to
`evals/reports/` and `docs/evals/`. The CI eval gate (`scripts/check_eval_gate.py`) fails when a prompt
marked `eval_required` lacks a report with its exact hash and lists the others as "not evaluated" (D-56).
See `evals/README.md`.
