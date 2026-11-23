# Runbook: the run worker (Part D)

The worker is the only process that executes runs (D-41). The API only records jobs, flags and decisions.

## Start and stop

| Action | How |
|---|---|
| Start everything | `./scripts/dev.ps1` starts the API, the UI and the worker. The worker starts after the API has migrated the database |
| Start the worker alone | `uv run python -m devteam_worker` |
| Process due jobs once, then exit | `uv run python -m devteam_worker --once` |
| Stop | Ctrl+C. The worker finishes the current step, then stops. A hard kill is also safe (see "Recovery") |

## The worker refuses to start when

| Message | Meaning and fix |
|---|---|
| `the state database is not at the current migration` | Start the API once (`./scripts/dev.ps1`): it backs up `data/state.db`, then migrates it |
| `cloud tracing is configured through environment variable(s) …` | A `LANGSMITH_*` / `LANGCHAIN_*` tracing variable is set. Unset it: traces stay on this machine (D-30) |
| `configured local models are not installed: …` | Install the models named in `config/defaults.yaml` (`ollama:` → `models:`), for example `ollama pull qwen2.5:3b`. The platform never downloads models itself |
| `no secure OS credential store is available` | Windows Credential Manager is required (D-18) |
| `another worker holds the lease` (exit code 3) | A worker is already running. Only one may run (one run at a time) |

If Ollama is not running, the worker still starts. Runs that reach an AI step wait ("waiting for resources") and continue when Ollama is back. Nothing is simulated in the meantime.

## Models (D-44)

- **Provider:** the local Ollama at `ollama.base_url`. Only loopback addresses are accepted; proxy environment variables are ignored.
- **Models:** one per slot, from `config/defaults.yaml`: `instruct` `qwen2.5:3b`, `coder` `qwen2.5-coder:3b`, `review` `qwen2.5:3b`, `fallback` `qwen2.5:1.5b` (Part A benchmark recommendation).
- **Limits:** from `config/budgets.yaml` (F-9, F-10).
- **Ollama settings:** set `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` (README) so only one model is in memory.

## Recovery after a crash or restart

On start the worker:

1. takes over a lease older than `orchestration.lease_ttl_s` (a dead worker's);
2. closes jobs the dead worker left running and queues one resume job for each affected run;
3. resumes each run from its last checkpoint (`data/checkpoints.db`).

Finished steps are not repeated. A task interrupted by the crash is recorded as `interrupted`, does not count as a failed attempt, and runs again. After more than `orchestration.max_crash_resumes` (3) crashes in a row, the task is blocked (D-40).

## Controls

Pause, resume and cancel are on the run's Build Progress page.

- **Pause and cancel** take effect at the next step boundary; the last checkpoint is kept.
- **Waiting runs:** a run that is waiting (for you or for resources) can be cancelled at once.

## Where to look

| What | Where |
|---|---|
| Progress | The run's Build Progress page (live), or `GET /api/projects/{project}/runs/{run}/events` |
| Trace | The Trace page: each step, its duration and outcome. Stored locally only |
| History | The History page: every status change and decision, from the activity log |
| Worker logs | The worker's console, and `data/logs/` when file logging is on |
