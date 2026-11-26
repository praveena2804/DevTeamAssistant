# Runbook: Capability Catalog and Feasibility Gate

## See what the platform can build now

Open **Settings → Capability Catalog**. Each entry shows its declared status (from
`capability_catalog/capabilities.yaml`), its effective status (computed now) and, if it is demoted, the
reason. The same data is available at `GET /api/settings/catalog`.

## A starter kit shows as demoted

A demoted kit means none of its entries can be offered: React, HTML, FastAPI, SQLite and no-login all
depend on the starter kits. The reason tells you which case applies.

| Reason | What to do |
|---|---|
| "has never been verified" | Run the starter-kit self-test: Sandbox page → *Verify*, or `POST /api/sandbox/templates/<id>/verifications` (D-49). The worker runs it in the sandbox |
| "latest verification … failed / blocked" | Open the verification on the Sandbox page, fix the cause (often the Docker engine; see `docs/runbooks/docker-setup.md`), then verify again |
| "is stale (the kit or an image changed)" | Verify again: the kit files or the locked images changed since the pass |
| "cannot be confirmed as current" | The image lock could not be read; check `sandbox/images/images.lock.json`, then verify again |
| "not available yet: arrives in Part K/P" | Expected: PostgreSQL arrives in Part K, GitHub delivery in Part P |

After a new passing verification, the next evaluation (and the next worker start) shows the entry as
available again. The worker audits the restoration as `catalog.entry_restored`.

## Decide at the feasibility gate

When a run waits at **Feasibility decision**, open the run, then *Open the feasibility report and decide*.

- **Proceed** continues with the buildable scope. Requirements blocked by feasibility leave the scope
  before the specification is drafted.
- **Below the threshold** (default 50%, `feasibility.min_coverage_pct` in `config/defaults.yaml`), tick
  the confirmation first.
- **Nothing to build**: when nothing can be built, Proceed is not offered.
- **Cancel** ends the run. Choose *revise* if you will upload a revised BRD and start a new run.
- **Substitutions** are shown as *offered*. You accept them in the clarification form, which arrives in
  Part I.

The report is in the workspace at `reports/FEASIBILITY_REPORT.md`. It can also be downloaded from the
Feasibility page; the API re-renders it from the records and checks the checksum.

## Change the catalog

A catalog change is a reviewed code change. Never edit the catalog to make a run pass.

1. **New capability.** Add a member to `CatalogKey` in `packages/core/src/devteam_core/contracts/catalog.py`,
   and an entry to `capabilities.yaml`. The file must list exactly the enum's keys.
2. **Supported entry.** It must name a starter kit that exists under `templates/target_apps/`, its build
   and test recipes (registered in `RECIPES`), and no credentials. Otherwise, set `available_from_part`.
3. **Aliases.** Add exact, lower-case phrases. They must be unique within the category, and none may be
   an ambiguous-database phrase.
4. **Schema.** Regenerate it with `uv run python scripts/export_contract_schemas.py` (CI runs it with
   `--check`).
5. **Tests.** Run `uv run pytest packages/services/tests/test_catalog.py packages/services/tests/test_feasibility.py`.

An invalid catalog stops the worker and the API at start-up, with the first problem named.

## Demonstrate a scenario without a real run

```text
uv run python scripts/evaluate_feasibility_fixture.py tests/fixtures/feasibility/power_apps.json --out <folder>
uv run python scripts/evaluate_feasibility_fixture.py <fixture> --out <folder> --state-db data/state.db
```

The script writes a report labelled *FIXTURE EVALUATION — not a run* and stores nothing (D-65).
