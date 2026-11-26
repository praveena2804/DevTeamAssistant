# Runbook: local models, routing, search index and evaluations (Part F)

## Models

| Task | How |
|---|---|
| See what is configured and installed | UI **Settings** page (read-only), or `uv run python scripts/doctor.py` |
| Install the approved models (once) | `ollama pull qwen2.5:3b`, `ollama pull qwen2.5-coder:3b`, `ollama pull qwen2.5:1.5b`, `ollama pull nomic-embed-text`. The platform never downloads models itself |
| Change which model serves a slot | Edit `config/models.yaml`. Only `approved: true` models with a `benchmark_ref` and a measured `peak_ram_mb` that fits the profile can be routed to; anything else stops start-up with the rule number |
| Per-agent or per-operation model | `agents:` (D-06) or `operations:` (D-37) in `config/models.yaml`: exactly one of `slot` or `model` |
| A run waits with `model.exhausted` | Every recovery step failed for resources (out of memory, too slow, load failure). Close other applications or free WSL memory (KI-039); the run resumes by itself |
| A run waits with `rate.limited` | The role's model-call rate limit (`config/rate_limits.yaml`) was reached; it continues when the bucket refills |
| Turn the answer cache on (D-28) | `llm.response_cache: true` (off by default). Hits are re-validated and shown as "cached" on the trace page |

## Search index (RAG)

| Task | How |
|---|---|
| Index a project | Automatic after an upload; the worker runs the job. Status: project page → **Search index** |
| Rebuild | **Rebuild index** button (or `POST /api/projects/{id}/knowledge/index`) |
| A job ends `blocked` with `rag.embedding_unavailable` | Install `nomic-embed-text` and start Ollama, then rebuild |
| A search returns `rag.index_stale` | The embedding model or chunker changed: rebuild (the old index is kept until the new one is ready) |
| The worker refuses to start: "ChromaDB environment variables are not allowed" | Remove every `CHROMA_*` variable from the environment (they could redirect the vector store) |
| Where the vectors live | `data/chroma/` (never committed). Deleting it is safe: rebuild each project's index |

## Evaluations

```powershell
uv run python scripts/run_evals.py --agent brd_analyst --dataset smoke   # real local model; owner-run
uv run python scripts/check_eval_gate.py                                 # offline; also runs in CI
```

Reports: `evals/reports/<agent>-v<N>.json` and `docs/evals/<agent>-v<N>.md`. A prompt marked
`eval_required: true` in `config/prompts.yaml` needs a report with its exact hash or CI fails.

## Prompts

Prompt files are immutable once released. To change a prompt: add `prompts/<agent>/<id>.v<N+1>.md`, record
its hash under `released` and set `active` in `config/prompts.yaml`, update the agent's `prompt_version`, and
run its evaluation. Editors must keep LF line endings and must not add a trailing newline (the hash is of the
exact bytes; a changed file stops start-up).
