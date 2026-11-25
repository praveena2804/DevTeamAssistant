# Part F — Plan: LLM Gateway, Model Routing, Context & Memory, and RAG

| Field | Value |
|---|---|
| Status | **Revision 2, approved (AP-023, 2026-11-25)**; all §9 decisions ruled as recommended |
| Date | 2026-11-25 |
| Milestone | M1 Harness & Orchestration (C, D, E approved) |
| Baseline | `main` = `efa5031` (v0.5.0, Part E approved and pushed, AP-022) |
| Sources | Roadmap §13 Part F (and H for boundaries), §5.1 HC table; Working Plan §20 Part F, HS-20…HS-24, GW-01…GW-12, §13.3; Spec §9.2–§9.6, §10, §21, §23 (#22), §25; ADR-0003; `docs/architecture/MODEL_ROUTING.md`; decisions D-06, D-12, D-28, D-35…D-38, D-41, D-44, D-46, D-47, D-49 |
| Branch (after approval) | `part/f-gateway-rag` (from `main`) |
| Revision 2 | Independent critical review (18 findings: 3 blockers, 8 major, 7 minor) applied; see §14 |

---

## 0. Summary

Part F completes the **governed model path** and adds **retrieval, context and evaluation infrastructure**:

1. **Router** from `config/models.yaml`: slot → model; per-agent (D-06) and per-operation (D-37) overrides; approved models only; `route_rule` recorded.
2. **Resilience**:
   - one effective timeout per call;
   - OOM and slow-model detection;
   - a recovery ladder (**shorter context first, then a smaller model**, Spec §9.4) that runs only after resource failures;
   - exhaustion → checkpoint + *waiting for resources* (Spec scenario #22).
3. **Records**:
   - a `ModelCall` table: tag + digest, prompt hash, tokens, duration, validation result and route rule, **never content**;
   - per-role rate limits;
   - a response cache (D-28) and an embedding cache.
4. **Service model calls**: embeddings, ingestion and evaluation calls also go through the gateway, under a **service principal and job budget**. Every model call is gateway-only, enforced by import contracts.
5. **File-backed prompt and skill registry** (`prompts/`, hashes, active versions).
6. **Evaluation framework** (runner, fixture format, scorers, reports) with a **CI eval-gate check**. The 8-BRD datasets stay in the agent parts (Part H onwards).
7. **RAG and context**:
   - local embeddings through the gateway;
   - **ChromaDB**, one collection per project, with per-document freshness;
   - retrieval returning chunk IDs and source references;
   - a token-budgeted **Context Manager**;
   - a read-only **project memory API**.
8. **Read-only Settings** page fed by a **worker-written inventory** (the API never contacts Ollama or Chroma).

**Agents: none.** No agent's behaviour changes. Agents start *using* retrieval and context in Part H onwards. Several roadmap F items were brought forward into D/E and are reused (§2).

---

## 1. Objectives, scope and exclusions

### 1.1 Objectives (roadmap AC-F-01…05)

- **AC-F-01:** every model call, including embeddings, goes through `LLMGateway` and is recorded (content-free).
- **AC-F-02:** external endpoints are impossible by configuration and are never contacted.
- **AC-F-03:** retrieval returns correct source references with project isolation.
- **AC-F-04:** Spec scenario #22 passes: a simulated OOM leads to a checkpoint and pause, with no external call.
- **AC-F-05:** the CI eval-gate check is active.

### 1.2 In scope

| Area | Items | IDs |
|---|---|---|
| Model path | Router and `models.yaml`; provider contract per GW-01 (`generate`, `embed`, `health`, `list_models`, `show`/digest, `unload`, `close`); future-adapter contract **disabled by policy** with HC-17 secret references (unused by Ollama) | GW-03 (finish), GW-04, GW-10 |
| Service calls | `EmbedRequest` contract; service principals and job budgets for embedding, ingestion and evaluation calls | GW-01/02 extension |
| Resilience | Single effective timeout; failure classification on HC-12 categories; recovery ladder (reduced `num_ctx` → smaller model); per-model free-RAM check before a call; circuit breaker; in-flight call cancellation; unload before switch; exhaustion → WAITING + checkpoint | GW-06, GW-11 (finish) |
| Usage and records | `ModelCall` table; token counts from Ollama; RAM sample; extended spans | GW-08, HS-21 |
| Limits and caches | Per-role rate limits; response cache (D-28); embedding cache | GW-09, HS-21 |
| Prompts and skills | `prompts/` files, exact hash rule, `config/prompts.yaml` active versions; file-backed skills (D-36) | HS-22 |
| Evaluation | `evals/` structure, fixture format, runner, scorers, reports (Working Plan §13.3 paths); **one smoke dataset** proving runner and gate; CI eval-gate | HS-22, HS-24 |
| Context and memory | Embedding service; ChromaDB per-project collections; ingestion from Part B chunks (source-agnostic interface); per-document freshness manifest; retrieval with metadata filters; Context Manager (per-role budgets); project memory API; `search_project_knowledge` v2 made available | HS-23 |
| Settings UI | Read-only page from SQLite (worker-written model inventory, limits, profile, index and eval status) | Working Plan F [IMPL] |
| Docs | `docs/architecture/{llm-gateway,context-memory,rag}.md`; benchmark update (embedding model, model-swap cost, worker RSS with ChromaDB); runbook | Roadmap deliverables |

### 1.3 Exclusions

| Not in Part F | Belongs to |
|---|---|
| **The 8 sample-BRD datasets with expected outputs** (Spec §9.6) | **Part H** (roadmap H "Evaluation"); each agent part adds its own (Working Plan §13.3 step 1) |
| Eval pass thresholds (D-12) | Agent parts H–O |
| Agents calling retrieval or the Context Manager; prompt quality rewrites | H–O |
| Any cloud or non-local provider (Spec §9.4: future, off by default) | Not MVP (only the disabled contract) |
| Fine-tuning or LoRA (Spec §9.5) | Deferred |
| Editing configuration from the UI | Not planned (D-55) |
| Knowledge sources beyond the project's BRD chunks (Spec §10.1 standards, framework docs, templates) | Parts that create them; F's ingestion interface is source-agnostic |
| Sandbox in the run graph (D-47) | Later build parts |

### 1.4 Reconciliation notes

- **"Stubs keep using the fake adapter in CI"** (roadmap) predates D-38. Since AP-016, runs use **real AI only**. The fake provider stays allowed only in harness unit tests (existing import contract). `respx` is an HTTP-level test double of the Ollama server, used only in tests; it is not a fake agent answer.
- **Already done:**
  - **GW-07** (cross-process lock): D-46 `ResourceLease`.
  - **GW-11** (unload + `/api/ps`): for sandbox runs, in the Part E Resource Guard.
  - **GW-05** (structured output, validation feedback retries): D-44 plus the executor.
  - **The loopback-only Ollama provider:** D-44.
- **A cache hit** replays an earlier *real* model answer that is re-validated. It is not a "prepared answer" in the D-38 sense, but the owner is asked to confirm this explicitly (D-28).

---

## 2. Current baseline and components to reuse

| Item | State today (verified) | Part F action |
|---|---|---|
| `providers/ollama.py` `OllamaProvider` | `/api/chat` with `format` = inlined JSON Schema; `/api/tags`, `/api/version`, `/api/ps`, `/api/generate` (`keep_alive: 0` unload); loopback-only + no credentials in URL; `httpx` with `trust_env=False`, no redirects | Add `/api/embed`, `/api/show` (digest); classify errors (OOM vs other 5xx, timeout, model missing); cancellable in-flight calls |
| `providers/__init__.py` `ModelProvider` Protocol | `name`, `generate` | Extend to GW-01 |
| `LLMGateway` (`gateway.py`) | Input guard; budget checks; lease (D-46) + `MODEL_LOCK` (released when the HTTP call really ends); transient retries (`RetryPolicy`); schema validation; audit `model.called`; span `llm.generate` | Router, recovery ladder, `ModelCall`, rate limits, cache, `embed()` |
| `ModelRequest` | Requires `role: AgentRole` and an operation ID prefixed by the role; task-scoped budget | Unchanged for agents; **new `EmbedRequest` and service requests** (§6.6) |
| Executor validation-feedback retries | Done (`retries.invalid_output: 2`) | Unchanged; attempt number recorded |
| `ModelSlot` enum; per-operation slot in `AgentSpec` | Done | Router resolves with precedence operation > agent > slot > global |
| `config/defaults.yaml` `ollama.models` (4 slots); `keep_alive: 1m` | Flat mapping | Moved to `models.yaml`; `keep_alive` default decided in D-59 |
| Budgets (`budgets.yaml`; `estimate_tokens` at ~3 chars/token); `resources.llm_context_tokens` unused | Done | Calibrated counting; Context Manager uses or replaces `llm_context_tokens` (recorded) |
| WAITING_FOR_RESOURCES path (executor → orchestration) | Done | Ladder exhaustion feeds it |
| `devteam_harness.ratelimit.RateLimiter` (token bucket, used by the API) | Done | Reused for per-role model limits |
| Prompt registry (`InMemoryPromptRegistry`; hash + tamper checks); 10 inline prompts in `orchestration/prompts.py` | Done in memory | File-backed registry; prompts moved with identical hashes (D-58) |
| `document_chunks` (Part B) with stable chunk IDs and references; `CHUNKER_VERSION` in `devteam_services.extractor.chunker` | Done; chunker version not stored per row | Chunker version recorded per document (§6.4) |
| `search_project_knowledge` tool (v1: no source filter; `timeout_s=5`) | Registered; raises "available from Part F"; granted to BRD Analyst, Requirements Finalizer and **Architect** | v2 schema with source filter; configurable timeout; `project_id` always from the task |
| Tracing (local SQLite exporter, allowlist) | Done | New attributes (route rule, digest prefix, cache hit, recovery step) |
| Benchmark script and 2026-11-19 baseline (Part A) | Done (no embedding benchmark) | Add the embedding model, swap cost and worker RSS |
| Resource lease and Resource Guard (E) | Done | Embedding and eval calls take the same `llm` lease |

---

## 3. Proposed architecture

```text
 Agent task (unchanged)                  Service jobs (worker / owner scripts)
 AgentExecutor ─ ModelRequest ─┐         RAG ingest/retrieve ─ EmbedRequest ─┐   Eval runner ─ ModelRequest(service) ─┐
                               ▼                                             ▼                                       ▼
                         LLMGateway.generate() / LLMGateway.embed()   ← the ONLY caller of providers (import contract)
   1 input guard · principal + budget (task budget, or service job budget) · per-role/per-service rate limit
   2 Router: operation > agent > slot > global → ModelChoice(tag, digest, num_ctx, timeouts, route_rule)
   3 response cache (D-28, if enabled): hit → re-validate → ModelCall(cache_hit) → return
   4 per-model free-RAM check (peak_ram_mb + margin) → else recovery ladder
   5 lease (D-46) + MODEL_LOCK; unload a different loaded model first (/api/ps verified)
   6 provider call with ONE effective timeout = min(model timeout, budget call timeout, provider request timeout)
   7 failure → classify (HC-12 category + code):
        transient (unreachable once, 503 busy) → existing RetryPolicy, same rung
        resource  (OOM, slow, load failure, timeout) → cancel in-flight call, verify unload,
                  next rung: reduced num_ctx → smaller model (ladder) → exhausted:
                  ResourceUnavailableError("model.exhausted") → WAITING + checkpoint (existing path)
        invalid output → returned to the executor (feedback retry); NEVER a ladder step
   8 validate → ModelCall row + audit model.called + span (content-free)
                         ▼
          OllamaProvider (loopback only): /api/chat /api/embed /api/show /api/ps /api/tags /api/generate(unload)

 RAG: document_chunks ─► Ingestor ─► EmbeddingService(gateway.embed, embedding cache)
      VectorStore = the only module importing chromadb (lazy import; PersistentClient pinned in code; telemetry
      off; no default embedding function) ── collection per project + chunker version + embedding digest
      IndexManifest (SQLite): per document → content checksum, chunker version, indexed_at; collection identity
      Retriever: embed query → Chroma query filtered by project (+ sources) → chunk IDs → re-read from SQLite
      ContextManager: per-role token budgets; ProjectMemory: read-only SQLite API

 Status: worker writes model inventory (tags, digests, Ollama version) + manifest summaries to SQLite;
         API reads SQLite only (never Ollama, never Chroma).
```

**Process placement:**

- **The API never makes model calls** and never opens Chroma (D-41/D-49 pattern; new import contract).
- **The worker** runs ingestion and re-index jobs and refreshes the model inventory.
- **Agents** reach retrieval only through the `search_project_knowledge` tool, inside the worker.
- **Owner scripts** (`scripts/run_evals.py`, `scripts/rag_query.py`) are composition roots that go through the gateway.

---

## 4. Implementation checkpoints

| Checkpoint | Content | Depends on |
|---|---|---|
| **F.1 Router, catalogue, gateway records and `embed()`** | `models.yaml` + schema + startup rules (MODEL_ROUTING §3.3); router with precedence and `route_rule`; GW-01 provider Protocol; `/api/show` digest; **`gateway.embed()` + `EmbedRequest` + service principals and job budgets**; `ModelCall` table (migration `0008_model_calls`); disabled future-adapter contract + HC-17 references; **import contracts** (providers only from gateway and composition roots; API never imports providers/rag/chromadb); `respx` suite part 1; the `llm` probe (schema-constrained call) | G-F; Q1 (models); D-06 |
| **F.2 Resilience, limits, caches** | Effective timeout; classification on HC-12; recovery ladder (reduced `num_ctx` → smaller model); per-model RAM check; circuit breaker; cancel in-flight + verified unload before stepping; exhaustion → WAITING + checkpoint; per-role rate limits; response cache (D-28) with re-validation and secret scan before write; embedding cache (project-scoped); token calibration from cold calls; scenario #22 at gateway **and** orchestration level | F.1; D-28, D-59 |
| **F.3 Prompt and skill registry** | `prompts/` files with front matter; exact hash rule; `config/prompts.yaml` active versions; `FilePromptRegistry`/`FileSkillRegistry`; move the 10 prompts verbatim (hash identity test); `.gitattributes` for `prompts/` | F.1; D-58 |
| **F.4 Evaluation framework and CI gate** | `evals/` structure (datasets, expected, reports); fixture format; runner (`python -m devteam_harness.evaluation run --agent … --prompt-version vN`); scorers; reports `evals/reports/<agent>-vN.json` + `docs/evals/<agent>-vN.md` (prompt hash + model digest); **one smoke dataset** (2–3 small cases from the existing Library Lending BRD fixture) for the BRD Analyst prompt; `scripts/check_eval_gate.py` in CI; eval-required flag per prompt (D-56) | F.3; F.2 |
| **F.5 RAG, Context Manager, project memory** | **Step 0: ChromaDB footprint and licence audit before `uv add`** (stop and report if it fails, D-54); embedding service (batches, `llm` lease); vector store (lazy import, pinned client settings); per-project collections; chunker version per document; manifest + jobs (migration `0009_rag_index`); worker index jobs; retriever (filters, SQLite cross-check); `search_project_knowledge` v2; Context Manager; project memory API; `llm` embedding round-trip | F.1, F.2; D-53, D-54 |
| **F.6 Settings, inventory, docs, validation** | Worker inventory → SQLite; read-only settings and status API + page; model-calls view on the trace page; docs; benchmark update; runbook; egress E2E in loopback jail; full regression A–E; completion report | F.1–F.5 |

Each checkpoint: implement → tests (security tests with named negative controls) → static gates → tracker rows → one local commit. G-F is requested after F.6.

---

## 5. Technical tasks and expected files

### F.1

- `config/models.yaml` + `devteam_core.config.models` (unknown keys rejected).
- `devteam_harness.llm_gateway.router` (`Router`, `ModelChoice`, `RouteRule`).
- **Startup rules** (MODEL_ROUTING §3.3), including:
  - every non-null slot resolves to an **approved** model with a `benchmark_ref`;
  - `embedding` may be **null** (then `embed()` and retrieval fail closed with `rag.embedding_not_configured`);
  - the embedding slot cannot be overridden per agent or operation;
  - ladders contain only approved models with lower `peak_ram_mb`;
  - no model's `peak_ram_mb` exceeds the profile's `max_model_ram_mb` (new profile key);
  - no inheritance cycles;
  - overrides name known agents and operation IDs.
- `providers/__init__.py` (GW-01 Protocol), `providers/ollama.py` (`embed`, `show`, `health`, `close`), `providers/policy.py` (adapter registry = Ollama only; any other adapter refused; HC-17 secret-reference field for future adapters).
- `contracts/llm.py`:
  - `EmbedRequest`: service principal, purpose (`rag.embed`), optional `project_id`, inputs as hashes plus texts held in memory only;
  - `ModelCall`: content-free, with a validator;
  - `RouteRule`.
- `devteam_harness.iam.principals`: service names `RAG_INGESTOR`, `EVALUATOR`.
- **Job budgets** (`budgets.yaml` `services:` section: calls, tokens, minutes per job).
- **Migration `0008_model_calls`** (append-only).
  - **Columns:** IDs, operation, agent/service, slot, model tag, digest, `route_rule`, `recovery_step`, prompt ID/version/hash, input hash, schema hash, `cache_hit`, tokens, duration, load_ms, free_ram_mb, outcome, error code, attempt.
  - **`project_id` is NULL only for platform service calls** (evaluation, model inventory probes), as `audit_events` and `sandbox_executions` already allow. That is the documented exception to the "every domain table carries `project_id`" rule.
- **Import contracts:**
  - `devteam_harness.llm_gateway.providers.*` may be imported only by `llm_gateway.gateway`, `orchestration.runtime`, the worker bootstrap and named scripts;
  - `devteam_api` never imports `llm_gateway.providers`, `rag.store` or `chromadb`.
- **Tests:**
  - router table;
  - one failing config per startup rule;
  - the respx provider suite;
  - `ModelCall` refuses content;
  - the import contracts, with planted violations;
  - `llm` probe (structured output on a probe schema);
  - **AC-F-01 metric**: respx route hits = `ModelCall` rows = `model.called` audit rows.

### F.2

- Classification lives in the existing `devteam_harness.resilience` (HC-12 `ErrorCategory`; codes `model.oom`, `model.slow`, `model.load_failed`, `model.timeout`, `model.exhausted`). Ollama 5xx with an out-of-memory message → `model.oom` (resource), not transient.
- **Order of handling:**
  1. A transient error retries the **same** rung (existing `RetryPolicy`, at most 2).
  2. A resource error goes straight to the next rung.
  3. Each rung step first cancels the in-flight HTTP call (closes the client connection) and verifies through `/api/ps` that the model is unloaded, so the next rung never waits behind a hung call.
- **Effective timeout** = `min(models.yaml timeout, budgets model_call_timeout_s, ollama.request_timeout_s)`. Connect, read and total timeouts are configured per model.
- **"Slow"** = first token or total time over the model's `slow_after_s` (config; default 60% of its timeout), recorded and counted by the breaker. A slow call still completes and is not treated as a failure.
- **Recovery ladder:** rung 1 = same model with reduced `num_ctx` (only if the request fits; the Context Manager trims retrievable context, never required input); rung 2… = smaller models from `fallback_ladders`.
- **Per-model RAM check** before every call: free RAM ≥ `peak_ram_mb` + `ram_margin_mb`.
- **Circuit breaker** per model (open after N resource failures, half-open after a cooldown).
- **Rate limits** (`config/rate_limits.yaml`, §6.5). An exceeded limit makes the call **wait** up to the bucket refill if that is within the call budget; otherwise it raises `ResourceUnavailableError("rate.limited")` → WAITING. Never silently dropped.
- **Caches:** `llm_response_cache` (D-28; off by default), `embedding_cache` (key = project + embedding digest + text hash). A secret scan runs before any write; text containing a detected secret is never cached.
- **Token calibration** uses only cold calls (`keep_alive: 0` or first load) to avoid KV-cache under-counting. Bounded; never under-estimates.
- **Tests:**
  - every failure kind;
  - retry versus step order;
  - cancel-before-step (a hung call does not block rung 2);
  - ladder exhaustion;
  - breaker;
  - RAM check;
  - rate limits;
  - cache keying, isolation, invalidation, re-validation, the secret refusal;
  - **scenario #22**: (a) gateway unit test with respx OOM responses; (b) an orchestration integration run reaches run status `waiting_for_resources` with a checkpoint row, then resumes when the respx server recovers; (c) a loopback socket jail records zero non-loopback connections.

### F.3

- `prompts/<agent>/<prompt_id>.v<N>.md`. Front matter holds the ID, version, agent, `eval_required` and notes.
- **Hash rule:** SHA-256 of the body bytes after the front-matter delimiter, UTF-8, LF line endings, trailing whitespace preserved exactly. `.gitattributes`: `prompts/** text eol=lf`.
- `config/prompts.yaml` (active version per prompt).
- `devteam_harness.prompts.files` (`FilePromptRegistry`, `FileSkillRegistry`). `orchestration/prompts.py` loads from the registry.
- Start-up refuses a missing file, a hash mismatch or a changed released version.
- Test: the 10 hashes equal the Part D hashes recorded in `model.called` audit rows.

### F.4

- **Structure:** `evals/datasets/<agent>/`, `evals/expected/<agent>/`, `evals/reports/`; `docs/evals/<agent>-vN.md`.
- `devteam_harness.evaluation`: `EvalCase`, `Runner` (service principal `EVALUATOR`, job budget, resumable), and the scorers:
  - schema validity;
  - citation validity against chunk IDs;
  - expected-item recall/precision;
  - forbidden content (e.g. invented technologies).
- **Smoke dataset:** 2–3 cases from the existing Library Lending fixture for `brd_analyst.analyse_section`, only to prove runner → report → gate end to end.
- **`scripts/check_eval_gate.py`** (offline, in CI): every **active** prompt version marked `eval_required: true` must have a report whose prompt hash equals the file hash and which records a model digest. Prompts marked `eval_required: false` are listed in the CI output as **"not evaluated (stub until its agent part)"**, so they are never silently passed. Thresholds are not enforced until D-12.
- `.github/workflows/ci.yml` (+1 step).

### F.5

- **Step 0 (before `uv add chromadb`):**
  - resolve the dependency tree in a throw-away environment;
  - licence audit (no AGPL; MPL listed);
  - `pip-audit`;
  - worker RSS with and without the import;
  - check that importing ChromaDB does not change the platform's OpenTelemetry tracer provider or exporters.

  Stop and report if any check fails (D-54).
- **`devteam_harness.rag`:**
  - `embedding` (batches; `llm` lease);
  - `store` (lazy import; `PersistentClient(path=data/chroma)` with settings pinned in code, `anonymized_telemetry=False`; `CHROMA_*` environment variables ignored; collections created without an embedding function);
  - `ingest`, `retrieve`, `manifest`.
- `devteam_harness.context` (`ContextManager`), `devteam_harness.memory` (`ProjectMemory`, read-only).
- **Migration `0009_rag_index`:** `rag_collections`, `rag_index_documents` (document → checksum, chunker version, indexed_at), `rag_index_jobs`; `document_chunks` gains a `chunker_version` column, backfilled from the current constant.
- **Worker:** `run_index_job_once`; a job is queued on extraction (API) and by the rebuild route.
- **`search_project_knowledge` v2:**
  - arguments: `query`, `sources` filter, `top_k` (bounded);
  - timeout from config (default 30 s, measured in the benchmark);
  - `project_id` always taken from the task context, never from arguments;
  - results are chunk IDs + references + text re-read from SQLite, labelled untrusted.
- **Config:** `rag:` (top_k, max_distance, batch size, timeouts) and `context:` per-role budgets (§6.5).
- **Tests:**
  - idempotent ingestion;
  - per-document freshness (a new upload is detected as unindexed);
  - rebuild on embedding-digest or chunker change;
  - project deletion removes the collection;
  - isolation;
  - budget truncation order;
  - references equal SQLite;
  - tool authorisation;
  - `llm` embedding round-trip.

### F.6

- **Worker:** `refresh_model_inventory` → `model_inventory` table (tags, digests, sizes, Ollama version, checked_at), in the same pattern as the sandbox status.
- **API (read-only, SQLite only):** `GET /api/settings/models`, `/api/settings/limits`, `/api/settings/profile`, `/api/projects/{id}/knowledge/index`, `POST /api/projects/{id}/knowledge/index` (queue a rebuild), `GET /api/runs/{id}/model-calls`. The web side gets `SettingsPage.tsx`, a model-calls table on the trace page, index status on the project page, and generated types.
- **Docs:** `docs/architecture/llm-gateway.md`, `context-memory.md`, `rag.md`; MODEL_ROUTING status; benchmark doc; runbook `docs/runbooks/models-and-rag.md`.
- **E2E:** upload → index job → tool retrieval in a worker process inside a loopback jail → status through the API.

---

## 6. Design details (the Working Plan's F [ARCH] list)

### 6.1 `models.yaml`

```yaml
models:
  qwen2.5:3b:       {approved: true, benchmark_ref: 2026-11-19-baseline, peak_ram_mb: 2144, num_ctx: 4096,
                     reduced_num_ctx: 2048, timeouts: {connect_s: 5, read_s: 180, total_s: 180}, slow_after_s: 110}
  qwen2.5-coder:3b: {approved: true, benchmark_ref: 2026-11-19-baseline, peak_ram_mb: 2148, num_ctx: 4096,
                     reduced_num_ctx: 2048, timeouts: {connect_s: 5, read_s: 180, total_s: 180}, slow_after_s: 110}
  qwen2.5:1.5b:     {approved: true, benchmark_ref: 2026-11-19-baseline, peak_ram_mb: 1208, num_ctx: 4096,
                     reduced_num_ctx: 2048, timeouts: {connect_s: 5, read_s: 120, total_s: 120}, slow_after_s: 75}
slots:
  instruct:    {model: qwen2.5:3b}
  coder:       {model: qwen2.5-coder:3b}
  review:      {model: null, inherit: coder}        # Spec §9.2 / MODEL_ROUTING §3.4
  coder_large: {model: null, inherit: coder}
  fallback:    {model: qwen2.5:1.5b}
  embedding:   {model: null}                         # set after D-53 is approved and benchmarked (F.5)
fallback_ladders: {instruct: [qwen2.5:1.5b], coder: [qwen2.5:1.5b], review: [qwen2.5:1.5b]}
agents: {}        # D-06
operations: {}    # D-37
keep_alive: 0s    # D-59 (today 1m)
ram_margin_mb: 512
```

The values are today's slot models. **Their formal approval is Q1** (Part A selection is still pending).

### 6.2 Recovery rules

- **Steps** happen only after resource failures: OOM, slow beyond the total timeout, timeout, load failure, or the RAM check failing.
- **Never a step after:**
  - invalid output (that stays with the executor's feedback retry);
  - a policy refusal or input refusal;
  - a rate limit.
- **Order:** reduced `num_ctx` (same model) → ladder models → `model.exhausted` → WAITING + checkpoint.
- **Every step is recorded** (`ModelCall.recovery_step`, audit, span).

### 6.3 Caches (D-28)

- **The response cache** is **off by default** (a config switch).
  - When it is enabled, the key includes the project, model digest, prompt hash, input hash, parameters and schema hash.
  - A hit is re-validated and recorded (`cache_hit`, shown in the model-calls view).
  - Nothing containing a detected secret is stored.
  - Entries are invalidated by a digest or prompt change.
- **The embedding cache** is always on and project-scoped.

### 6.4 RAG rules (Spec §10)

- SQLite is the source of truth; Chroma holds vectors and metadata only.
- **Collection name:** `p_<project>__c<chunker version>__e<embedding digest prefix>`. A change of model or chunker creates a new collection and a rebuild; the old collection is deleted after success. Retrieval refuses a collection whose identity does not match the manifest (no stale vectors).
- **Freshness** is per document (checksum + chunker version). Retrieval reports `index_incomplete` while documents are pending.
- **Queries** are always filtered by `project_id`; results are cross-checked with SQLite and anything missing is dropped and logged.
- **Embedding** runs in batches under the `llm` lease. Ollama is recommended to load one model at a time, and the swap cost is measured in the benchmark.

### 6.5 Limits and budgets (proposed defaults; final values after the benchmark)

| Item | Default |
|---|---|
| Rate limit per agent role | 12 model calls/min (burst 3) |
| Rate limit, `RAG_INGESTOR` | 120 embedding batches/min |
| Rate limit, `EVALUATOR` | 12 calls/min |
| Embedding batch | 16 chunks |
| `top_k` (default / max) | 6 / 12 |
| Context budget, discovery roles (BRD Analyst, Finalizer) | input 3,072 tokens: required input first, retrieval ≤ 1,536, memory ≤ 256 |
| Context budget, build roles | input 3,072: required input first, retrieval ≤ 1,024, memory ≤ 512 |
| Context budget, Documenter | input 3,072: records first, retrieval ≤ 768 |
| Service job budget, ingestion | 2,000 calls, 30 min per job |
| Service job budget, evaluation | per dataset, 60 min per run, resumable |

### 6.6 Service calls (non-agent)

- **Identity and budget:** `EmbedRequest` and service `ModelRequest` carry a service principal (`service:rag_ingestor`, `service:evaluator`), a job ID and a job budget instead of an agent task.
- **Gate and records:** they pass the same gateway steps and produce `ModelCall` rows (`agent` NULL, `service` set).
- **Separate from agent tasks:** agents can never use a service principal. Today the principal types keep them apart; F.1 adds an explicit gateway check and a test.

---

## 7. Security considerations and failure handling

| Risk | Control | Named negative control |
|---|---|---|
| Egress (AC-F-02) | Loopback check at config load and in the provider; adapter registry = Ollama only; import contracts (providers only from the gateway and composition roots; ChromaDB only in `rag.store`; the API imports neither); ChromaDB telemetry off; no default embedding function; `CHROMA_*` environment variables ignored; E2E in a loopback socket jail | Telemetry flag flipped in a test build → the jail test records a connection attempt and fails; a planted import violation → lint-imports fails |
| Content in records and logs | `ModelCall` has no content fields (validator); caches only under `data/`; span allowlist | A content field added to a sample → contract test fails |
| Cross-project leakage | Per-project collections + mandatory filter + SQLite cross-check; project-scoped cache keys | Project filter removed in a test double → the isolation test fails; cache project key dropped → the cache test fails |
| Prompt injection through retrieved text | Labelled untrusted; never in system messages | A chunk containing "ignore previous instructions" stays in a user message (test) |
| Tampered prompts or skills | Hash checks at start-up and per call | Edited file → start-up refusal test |
| Secrets in caches | Secret scan before cache writes | A planted token → not cached (test) |
| Supply chain (ChromaDB) | Step 0 audit; lock; `pip-audit`; licence report; lazy import | — |
| Memory pressure | Per-model RAM check; lease; unload before switch; recovery ladder; WAITING | Simulated low RAM → WAITING (test) |
| Fail closed | Unapproved/missing model → start-up refusal; null embedding slot → retrieval refused; manifest mismatch → retrieval refused; missing report for an `eval_required` prompt → CI fails | Each has a test |

---

## 8. Database, API and UI impact

- **Migrations** (automatic backup before migrating; rehearsal on a copy of the real DB):
  - `0008_model_calls`: `model_calls`, `llm_response_cache`, `embedding_cache`, `model_inventory`.
  - `0009_rag_index`: `rag_collections`, `rag_index_documents`, `rag_index_jobs`, plus `document_chunks.chunker_version`.
- **API:** read-only routes and one queue route (§5 F.6). All routes read SQLite only.
- **UI:**
  - Settings page (models, digests, approved/installed, profile, limits, eval-gate status);
  - model calls on the trace page;
  - index status on the project page.

---

## 9. Decisions requiring owner input

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **Q1** | Model approval (Part A selection still pending) | Approve today's slot models (`qwen2.5:3b`, `qwen2.5-coder:3b`, fallback `qwen2.5:1.5b`), or choose others | Approve today's models (benchmarked 2026-11-19, used in D/E). F.1 cannot start without it |
| **D-06** | Per-agent model overrides | (a) none; (b) allowed in `models.yaml` with validation | (b), as in MODEL_ROUTING.md; empty by default |
| **D-28** | LLM response cache | (a) off; (b) available, local, digest+prompt+input keyed, re-validated, per project, **off by default** | (b); and please confirm that a re-validated cache hit is not a "prepared answer" under D-38 |
| **D-53** (new) | Embedding model | `nomic-embed-text` (Spec example) or `all-minilm` (smaller) | `nomic-embed-text` after the F.5 benchmark; you run `ollama pull` (models are never downloaded automatically); slot stays null until then |
| **D-54** (new) | ChromaDB integration | (a) in-process `PersistentClient`, lazy import, telemetry off, own embeddings; (b) Chroma server in WSL; (c) another vector library (a spec deviation) | (a), conditional on the F.5 step-0 audit; stop and report if it fails |
| **D-55** (new) | Settings UI | (a) read-only; (b) editable | (a) |
| **D-56** (new) | Eval gate in F | (a) reports required for prompts marked `eval_required`; Part D stub prompts marked `false` and listed as "not evaluated" until their agent part; (b) all 10 active prompts need reports now (≈80 local runs on prompts that H–O will rewrite) | (a) |
| **D-57** (new) | Where RAG queries run | (a) worker/tools + owner script; (b) also an API route | (a) |
| **D-58** (new) | Move the 10 inline prompts to `prompts/` | (a) verbatim with identical hashes; (b) leave inline until H | (a) (a small, recorded change to Part D code) |
| **D-59** (new) | Ollama `keep_alive` default | (a) `0s` (unload after each call; MODEL_ROUTING, Spec §9.3 "unload quickly"); (b) keep `1m` | (a) on `low_memory`; the benchmark shows the reload cost |
| — | `respx` (MIT) as a new **dev** dependency | approve / hand-written transports | approve |

---

## 10. Testing strategy and acceptance criteria per checkpoint

| Checkpoint | Tests | Measurable acceptance |
|---|---|---|
| F.1 | Router table; one failing config per startup rule; respx provider suite; `ModelCall` content refusal; import contracts with planted violations; `llm` probe | Every call has a `route_rule`; unapproved/unknown model refuses start-up; respx hits = `ModelCall` rows = audit rows (100%) |
| F.2 | All failure kinds; retry vs step; cancel-before-step; breaker; RAM check; rate limits; cache suite; scenario #22 (gateway + orchestration + jail) | **AC-F-04**: run status `waiting_for_resources`, checkpoint row present, resume works, zero non-loopback connections; no step after invalid output (test) |
| F.3 | Hash identity with Part D audit rows; tamper; missing version; skills grant nothing | 10/10 prompts load from files with Part D hashes |
| F.4 | Scorer units; runner on the smoke dataset (`llm`); gate with missing/stale/complete reports | **AC-F-05**: the CI step fails for an `eval_required` prompt without a matching report and lists stubs as "not evaluated" |
| F.5 | Step-0 audit evidence; ingestion idempotent; freshness; rebuild; isolation with negative control; references equal SQLite; budget order; telemetry-off in jail; `llm` embedding round-trip | **AC-F-03**: 100% of results carry the correct project and reference; 0 cross-project results |
| F.6 | API security matrix; web tests; E2E in loopback jail; full regression A–E (incl. `llm`, `docker`, `slow`, keep-awake); coverage | **AC-F-01** across the E2E (every call, including embeddings, recorded); **AC-F-02** (no external endpoint configurable or contacted) |

Real-model tests run with a keep-awake request (KI-043).

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| ChromaDB footprint or licences fail the audit | F.5 blocked | Step 0 before adding; stop and report (D-54) |
| Embedding model + LLM swaps are slow or exceed memory | Waits, slow ingestion | Same lease, batches, `keep_alive` policy, per-model RAM check; measured |
| A hung HTTP call blocks the recovery ladder | False exhaustion | Cancel in flight and verify unload before stepping (tested) |
| Moving prompts changes hashes | Breaks traceability | Exact hash rule + identity test + `.gitattributes` |
| Cache hides a model change | Wrong reuse | Digest in key; off by default |
| Real-model tests disturbed by sleep or memory (KI-039, KI-043) | Flaky evidence | Keep-awake; WSL cache drop; environment recorded |
| Scope creep into agent behaviour or datasets | Part H work early | Exclusions §1.3; test that agent behaviour files are unchanged |

---

## 12. Git workflow and completion criteria

- **Branch:** `part/f-gateway-rag` from `main` (`efa5031`); one local commit per checkpoint; Conventional Commits.
- **No push, merge or tag** without G-F approval and a separate push instruction. No force-push.
- **Database:** backed up before every migration; rehearsal on a copy.
- **Definition of Done:**
  - AC-F-01…05 with evidence;
  - full regression A–E green (keep-awake);
  - coverage ≥ 90% for harness and safety-critical modules, ≥ 80% elsewhere;
  - all static gates;
  - `pip-audit`, `npm audit`, gitleaks and the licence audit clean;
  - egress check;
  - docs and both trackers;
  - `docs/parts/PART-F-COMPLETION-REPORT.md`;
  - **stop for G-F**.

---

## 13. Critical review: own findings (revision 1)

| Finding | Resolution |
|---|---|
| GW-03/05/07/11 largely done | Marked as reuse (§2) |
| Roadmap "fake adapter in CI" vs D-38 | Reconciled (§1.4) |
| An API RAG route would make the API call models | Excluded (D-57) |
| Eval thresholds would pull agent work forward | Stay with D-12 |
| Moving prompts touches Part D code | Minimal, hash-identical, D-58 |
| ChromaDB weight, telemetry, model downloader | Import contract, telemetry off, no default embedding function, jail test, audit |
| Model approval pending since Part A | Q1 blocks F.1 |

## 14. Independent review (revision 2) — findings and resolutions

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Blocker | 8-BRD eval dataset is Part H's deliverable and does not exist; scorecards for 10 stub prompts = ~80 runs on prompts H–O will rewrite | Datasets moved to H (§1.3); F builds the framework + a smoke dataset; gate uses `eval_required` and lists stubs as "not evaluated" (D-56) |
| 2 | Blocker | Embedding, ingestion and eval calls have no agent, task or budget (`ModelRequest` requires an agent role) | `EmbedRequest`, service principals and job budgets in F.1 (§6.6); `ModelCall.project_id` NULL only for platform service calls (documented) |
| 3 | Blocker | `models.yaml` example failed its own startup rules (unapproved embedding model) | `embedding: {model: null}` until D-53; retrieval fails closed while null |
| 4 | Major | `review` slot inherited from the wrong slot | `inherit: coder` (Spec §9.2) |
| 5 | Major | Timeouts, retries and ladder collide; hung call holds `MODEL_LOCK`; three timeout sources | Retry-vs-step order, cancel + verified unload before stepping, one effective timeout, HC-12 categories (§5 F.2) |
| 6 | Major | Spec's "shorten context" step and per-model RAM check missing | Reduced-`num_ctx` rung + per-model RAM check (§6.2) |
| 7 | Major | AC-F-01 not enforced structurally | Import contracts + measurable hits = rows = audit (F.1) |
| 8 | Major | Settings/status would make the API contact Ollama and Chroma | Worker-written inventory and manifest in SQLite; API contract (F.1/F.6) |
| 9 | Major | Index freshness and chunker version not covered | Per-document manifest; chunker version per chunk row; in the collection name (§6.4) |
| 10 | Major | Working Plan [ARCH] items left undefined; report paths contradicted Working Plan §13.3 | §6.5 limits and budgets; §6.3 cache policy; §6.4 naming; report paths per §13.3 |
| 11 | Major | ChromaDB weight under-assessed | Step 0 audit (RSS, OTel side effects, licences), lazy import, pinned client settings, `CHROMA_*` env ignored |
| 12 | Minor | Search tool timeout 5 s too short; no source filter; project from arguments | Tool v2: configurable timeout, source filter, project from task only |
| 13 | Minor | Embedding cache not project-scoped; secrets in cache; cache hit vs D-38 | Project in key; secret scan before write; D-38 confirmation in D-28 |
| 14 | Minor | Token calibration biased by KV cache; `keep_alive` default | Cold calls only; D-59 |
| 15 | Minor | Missing `llm` suite items, orchestration-level #22, named negative controls, slow threshold | Added (§5, §7, §10) |
| 16 | Minor | Factual slips (§3.3 not §5; Architect also has the tool; `RateLimiter` location; unload endpoint; Chroma path; `max_model_ram_mb`) | Corrected |
| 17 | Minor | Prompt hash rule unspecified | Exact rule + `.gitattributes` (F.3) |
| 18 | Minor | `gateway.embed()` had no checkpoint before its cache | `embed()` in F.1 |
