# Prompt evaluations (HC-09)

Every active prompt version is evaluated against a dataset before it may be used once its agent part makes
it `eval_required` (D-56). The framework arrived in Part F; the real datasets arrive with each agent part
(Part H brings the 8 sample BRDs of Spec §9.6).

| Folder | Content |
|---|---|
| `datasets/<agent>/<dataset>/<case_id>.json` | One case: `operation_id`, `slot`, `output_schema`, `data_label`, and `data` (the exact DATA text the agent sends, e.g. numbered BRD pieces) |
| `expected/<agent>/<dataset>/<case_id>.json` | What to check: `max_citation`, `required_terms`, `forbidden_terms`, `expect_found` |
| `reports/<agent>-v<N>.json` | The scorecard: prompt hash, model tag and digest, dataset hash, per-case scores and totals (with `docs/evals/<agent>-v<N>.md`) |

**Run** (owner, real local model; never in CI):

```powershell
uv run python scripts/run_evals.py --agent brd_analyst --dataset smoke
```

**Gate** (CI, offline): `uv run python scripts/check_eval_gate.py`. It fails when a prompt marked
`eval_required: true` in `config/prompts.yaml` has no report with its exact hash, and lists the other
prompts as "not evaluated". Pass thresholds are set per agent part (D-12).

The `smoke` dataset (3 cases from the Library Lending fixture) only proves the runner, the report and the
gate end to end. It is not a quality benchmark.
