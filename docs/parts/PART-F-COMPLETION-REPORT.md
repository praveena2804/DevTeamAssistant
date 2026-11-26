# Part F — Completion Report

LLM Gateway, Model Routing, Context & Memory, and RAG · Gate **G-F** · Date: 2026-11-25

| Field | Value |
|---|---|
| Status | **Implemented, awaiting the owner's G-F review.** Pushed as branch `part/f-gateway-rag`; not merged into `main`, not tagged, not released |
| Branch | `part/f-gateway-rag` (from `main` `efa5031`, v0.5.0) |
| Commits | `58b00cc` F.1–F.2 · `7d2ad66` F.3 · `059295c` F.4 · `d81e856` F.5 · F.6 + this report: see §10 |
| Rulings applied | AP-023 (plan rev. 2 approved; Q1 models, D-06, D-28, D-53…D-59, `respx`), AP-024 (ChromaDB accepted in-process only; KI-045) |
| Plan | `docs/parts/PART-F-PLAN.md` revision 2 (independent review: 18 findings applied before implementation) |

## 1. What Part F delivers

1. **Model routing** (`config/models.yaml`, `devteam_harness.llm_gateway.router`): approved models only, slots
   with inheritance, per-agent and per-operation overrides (empty by default), nine start-up rules, the routing
   rule recorded on every call.
2. **Resilience**: one effective timeout; out-of-memory, load-failure, timeout, RAM and circuit-breaker
   failures step down the recovery ladder (reduced context, then the smaller model); invalid output never does;
   a timed-out call is cancelled and its model unloaded before the next rung; exhaustion makes the task wait
   for resources and the run checkpoint (Spec scenario #22).
3. **Records**: every model call — agent generations, embeddings, evaluation calls — goes through the gateway
   and produces a content-free `ModelCall` row (migration 0008) plus an audit event and a span; installed model
   digests come from the local Ollama.
4. **Limits and caches**: per-role and per-service rate limits (wait, never drop); response cache (D-28, off by
   default, re-validated, project-scoped, never stores secrets); per-project embedding cache.
5. **Prompts and skills**: file-backed, hash-verified registry; the ten Part D prompts moved verbatim (hashes
   unchanged); skills grant no permission.
6. **Evaluation**: runner, deterministic scorers, scorecards, smoke dataset; **CI eval gate** active.
7. **Retrieval**: local `nomic-embed-text` embeddings (benchmarked) through the gateway; in-process ChromaDB;
   one collection per project and identity; per-document freshness; rebuild on model or chunker change;
   SQLite cross-check of every hit; `search_project_knowledge` v2; Context Manager; read-only project memory.
8. **Settings and status** (read-only, D-55): Settings page (models, digests, slots, profile, limits, eval
   status), search-index panel on the project page, model calls on the trace page. The API reads SQLite and
   configuration only; it never contacts Ollama or the vector store (D-57).

**Agents: none.** No agent behaviour changed. Agents start using retrieval and context in Part H.

## 2. Acceptance criteria

| Criterion | Result | Evidence |
|---|---|---|
| AC-F-01 Every model call goes through the gateway and is recorded | **Met** | Import contracts (only the composition roots build the provider; rag/context/memory/evaluation never reach a provider; the API never imports the gateway); respx: HTTP calls = `ModelCall` rows = audit rows, for successes and failures; E2E-F: every embedding recorded as `embed / rag_ingestor / valid` |
| AC-F-02 External endpoints impossible by configuration | **Met** | Loopback-only check at config load and in the provider (Part D); adapter registry Ollama only; ChromaDB `CHROMA_*` and OpenTelemetry exporter variables refused; loopback socket-jail tests (gateway scenario #22, ChromaDB, E2E-F) with negative controls: no outbound connection |
| AC-F-03 Retrieval returns correct source references with project isolation | **Met** | `test_rag.py` (references equal SQLite; own-project hits only; a store that ignores the filter is caught by the SQLite cross-check); same on the real ChromaDB; E2E-F with the real embedding model |
| AC-F-04 Spec scenario #22 (simulated OOM → checkpoint/pause, no external call) | **Met** | `test_scenario_22.py`: run `waiting_for_resources`, checkpoint saved, resume job queued, ladder tried (3B 4096 → 3B 2048 → 1.5B), only `127.0.0.1` contacted; resume re-enters the step |
| AC-F-05 The CI eval-gate check is active | **Met** | `scripts/check_eval_gate.py` in `ci.yml`; tests: fails for an `eval_required` prompt without a matching report, a stale hash or a malformed report; passes with a matching one; lists stubs as "not evaluated" |

## 3. Tests and results

| Run | Result |
|---|---|
| F.1–F.2 targeted (router 17, gateway respx 19, import rules 5, scenario #22 2) | all passed (TR-0133) |
| F.1 live probe (real Ollama) | 1 passed: routed to `qwen2.5:1.5b`, schema-valid, real digest recorded (TR-0134) |
| F.3 prompt registry | 56 passed; 10/10 prompt hashes equal Part D (TR-0137) |
| F.4 evaluation | 8 passed; real-model smoke evaluation 3/3 cases, all scores 1.0 (TR-0138, TR-0139) |
| F.5 RAG (test store 11 + real ChromaDB 1 + ChromaDB controls 5 + worker 2) | all passed (TR-0142) |
| F.6 API (settings/knowledge 4, security matrix 30), web (24 tests, lint, typecheck, build) | all passed |
| E2E-F (real API + real worker process in the network jail + real embeddings + real ChromaDB) | 1 passed (12 s) |
| Full regression on the branch (every marker except `acceptance`) | **2,591 passed, 1 failed, 1 skipped (KI-020)** of 2,593 in 86 min 23 s (TR-0146). The one failure is the Part E S-3 #5 idle-engine test (`assert 28 >= 29` engine checks in its 30-minute window): Windows entered Modern Standby twice during that window (22:26–22:30 and 22:56–22:59, Kernel-Power events 506/507) despite the keep-awake request, so two one-minute checks were lost; every check that ran reached the engine (KI-043). Re-run alone: **1 passed** (30 min 22 s, display keep-awake, no standby), engine blocked after `wsl --shutdown` and restored (TR-0146). |
| Coverage | **93.3%** overall. Packages: harness 95.6% (LLM gateway 90.2%, RAG 94.8%), core 97.4%, state 97.7%, services 94.1%, orchestration 90.2%, API 97.0%; worker app 68.1% (its run loop runs in a subprocess, as in Part E). New modules: `routers/settings.py`, `routers/knowledge.py`, `tools/builtin.py`, `memory`, `rag/__init__` 100%; `rag/store.py` 98.4%; `context` 98.5%; `rag/retrieve.py` 94.9%; `repositories/rag.py` 95.2%; `evaluation` 92.6%; `repositories/model_calls.py` 90.0%; `gateway.py` 89.8%; `rag/ingest.py` 88.6%; `router.py` 87.5%; `config/models.py` 86.9%; `limits.py` 86.4%; `resilience.py` 85.5%; `cache.py` 85.1%; `budgets/jobs.py` 84.9%; `prompts/files.py` 84.1%; `providers/ollama.py` 81.8% (package targets met; the per-module gaps are mostly defensive error branches) |

## 4. Security and supply chain

- **ChromaDB (KI-045, AP-024):** 4 advisories without a fix, all in ChromaDB's server mode and auth provider,
  which the platform never runs. Accepted in-process only; exactly these IDs are ignored in `pip-audit`
  (`security.yml`, with the reason); only `rag.store` may import it; no built-in embedding function; telemetry
  off; `CHROMA_*` refused; no outbound connection (socket jail). Re-check at every part.
- **OpenTelemetry exporter (DV-033):** ChromaDB pulls in the OTLP exporter. The Part C presence check was
  replaced by controls on use: no platform import of an exporter (static scan, negative control),
  `OTEL_*EXPORTER*` variables refused, the tracer provider is unchanged after ChromaDB is used, and the platform
  exports only to SQLite.
- **Audits:** `pip-audit --strict` on 160 locked packages: no other known vulnerability; `npm audit`: 0;
  `gitleaks` (history and tree): no leaks; licences: no AGPL/GPL; MPL-2.0: certifi, orjson (ruled), tqdm (new,
  accepted with ChromaDB); §2 inventory regenerated (134 runtime packages).
- **Content never recorded:** `ModelCall` has no content field (contract test); caches only under `data/`; span
  attributes allow-listed (5 new IDs).

## 5. Decisions and deviations

Ruled: Q1, D-06, D-28, D-53, D-54 (+ AP-024), D-55, D-56, D-57, D-58, D-59. Deviations recorded: DV-029 (F.1/F.2
one commit; earlier tests updated for the new head/config; `keep_alive` in settings; digest from `/api/tags`),
DV-030 (Part D prompts moved; runtime test), DV-031 (`scripts/run_evals.py` instead of `python -m …`), DV-032
(search tool v2), DV-033 (exporter presence check replaced).

## 6. Database

Migrations `0008_model_calls` and `0009_rag_index` (up/down tested; `document_chunks.chunker_version`
backfilled with "1"). Rehearsal on a copy of the real `data/state.db` (read-only source, SQLite backup API): 0004 → 0009 → 0008 → 0009; row counts unchanged; audit chain intact at each step (3 chained, 5 pre-chain); RAG tables removed by the downgrade; **real file byte-identical** (TR-0144). The real `data/state.db` is migrated (with a backup) by the API at its next
start (KI-034).

## 7. Known issues and limitations

| ID | Item |
|---|---|
| KI-045 | ChromaDB server-side advisories accepted (in-process only); re-check every part |
| KI-039, KI-043 | Low memory and laptop standby can disturb real-model tests; run them with keep-awake |
| KI-044 | Windows 260-character path limit (existing Part C limit) |
| — | Agents do not use retrieval or the Context Manager yet (Part H); the 8-BRD evaluation datasets arrive with Part H (D-56); eval thresholds per agent part (D-12) |
| — | ChromaDB keeps its files open until the process ends on Windows: the worker owns the store; deleting `data/chroma/` needs the worker stopped |
| — | The real database is still at revision 0004 until the API's next start (KI-034) |

## 8. Parts A–E remain intact

The full regression ran every Part A–E test, including the real-model (`llm`), Docker sandbox (`docker`) and long (`slow`) suites: the Part D kill-and-restart run (AC-D-02), the real-model graph runs, E2E-1, E2E-2, E2E-3 and the starter-kit and recipe runs in the real sandbox all passed. Earlier tests changed by Part F are recorded as deviations (DV-029, DV-030, DV-032, DV-033); no test was skipped, weakened or deleted to pass. The only failure is the standby-affected S-3 #5 timing count described in §3.

## 9. Branch and push

Pushed `part/f-gateway-rag` to `origin` (normal push, no force); remote ref verified with `git ls-remote` equal to the local branch head. Not merged into `main`, no tag, no release. The exact pushed hash and the verification are recorded in the tracker (TR-0147).

## 10. Commits

| Checkpoint | Commit |
|---|---|
| Plan approval record + F.1–F.2 router, records, embeddings, resilience, limits, caches | `58b00cc` |
| F.3 prompt and skill registry | `7d2ad66` |
| F.4 evaluation and CI gate | `059295c` |
| F.5 retrieval, ChromaDB, context, memory | `d81e856` |
| F.6 settings/status API and UI, E2E-F, documentation, this report | the F.6 commit on `part/f-gateway-rag` (hash recorded in tracker TR-0147) |

**Part G has not been started.** Waiting for your G-F decision.
