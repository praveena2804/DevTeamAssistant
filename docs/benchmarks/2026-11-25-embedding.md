# Embedding model benchmark — 2026-11-25 (Part F.5, D-53)

| Field | Value |
|---|---|
| Model | `nomic-embed-text` (digest `0a109f422b47…`), installed by the owner's approved setup (AP-023) |
| Server | Ollama 0.40.18, loopback only, `/api/embed` |
| Machine | 16 GB, CPU only (the `low_memory` profile) |
| Method | `scripts`-style probe through the real local Ollama: models unloaded first; 16 chunks of 1,074 characters (close to `chunk_target_chars` 1,200) per batch |

## Results

| Measure | Value |
|---|---|
| Vector size | 768 dimensions |
| Resident size (`/api/ps`) | 371 MB |
| Drop in free RAM while loaded | 474 MB → catalogue `peak_ram_mb: 480` |
| Cold load + first batch of 16 | 8.5 s |
| Warm batch of 16 | 6.5 s (about 2.4 chunks per second) |
| Single query (retrieval) | 0.54 s warm |
| Swap from the 3B chat model (unload, then first query) | 0.32 s + 0.55 s |

## Conclusions

- **Approved for the embedding slot** (`config/models.yaml`): small enough to load next to nothing else (one
  model at a time), and a swap from the chat model costs under a second.
- **Indexing speed:** a typical BRD of 40–120 chunks indexes in about 20–60 s on this laptop; the worker does
  it as a background job, never inside an agent step.
- **Retrieval timeout:** a query costs ~0.5 s warm and ~1 s after a swap; the `search_project_knowledge`
  tool timeout is 30 s (generous margin for a cold load under memory pressure).
- Raw numbers: kept with the tracker record TR-0140.
