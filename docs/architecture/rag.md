# Retrieval over project knowledge (Part F.5; HC-08; Spec §10)

## Rules

- **SQLite is the source of truth.** ChromaDB (in-process, `data/chroma/`, D-54) holds only vectors and
  metadata (`project_id`, `document_id`, `ordinal`). Every hit is re-read from `document_chunks` restricted to
  the same project; text and references always come from SQLite.
- **One collection per project and identity**: `p_<project>__c<chunker version>__e<embedding digest prefix>`.
  A new embedding model digest or chunker version builds a new collection in full; the old one is retired only
  after the new one is ready, so retrieval never sees a half-built index.
- **Freshness per document**: `rag_index_documents` records each document's content checksum and chunker
  version. A new or changed upload is reported (`index_incomplete`) until the worker indexes it.
- **Fail closed**: no vector store, no ready collection, or a collection built with another embedding model or
  chunker → `ResourceUnavailableError` (`rag.store_unavailable`, `rag.index_unavailable`, `rag.index_stale`).
  Nothing is retrieved from stale vectors.
- **Embeddings only through the gateway** (`gateway.embed`) as `service:rag_ingestor` under a job budget,
  batched, under the same lease as model calls (never alongside an LLM call or a sandbox run). Embedding model:
  `nomic-embed-text` (D-53; `docs/benchmarks/2026-11-25-embedding.md`), never overridden per agent.

## Flow

| Step | Who | What |
|---|---|---|
| Upload extracted | API | queues an `index` job (`rag_index_jobs`); the API never embeds (D-57) |
| Index job | worker | `Ingestor.index_project`: skip unchanged documents, embed changed ones (batches of 16), delete removed ones, record the manifest; documents chunked by another chunker version are skipped (re-extract first) |
| Rebuild | person → API → worker | `POST /api/projects/{id}/knowledge/index` queues a `rebuild` (one active job per project) |
| Search | agent tool → worker | `search_project_knowledge` v2: query (+ optional document filter); the project always comes from the task. Returns chunk IDs, references, text (untrusted data) and scores |
| Status | API | `GET /api/projects/{id}/knowledge/index` (manifest + latest job) |

## ChromaDB in-process (D-54 (a); KI-045, AP-024)

Only `devteam_harness.rag.store` imports `chromadb` (import contract with planted-violation tests), lazily.
It uses `PersistentClient` with anonymised telemetry off, never creates or uses a built-in embedding function
(collections are created with `embedding_function=None`), and refuses to start if any `CHROMA_*` environment
variable is set. Tests prove no outbound connection inside a loopback-only socket jail (with a negative
control). Four published ChromaDB advisories affect only its HTTP server and RBAC auth provider, which the
platform never runs; they are ignored by ID in `pip-audit` with this justification and re-checked each part.

## Isolation (AC-F-03)

Queries always carry the `project_id` filter; results are confirmed against SQLite for the same project. A
test with a store that ignores the filter (negative control) shows foreign vectors are returned by the store
but never reach the caller.
