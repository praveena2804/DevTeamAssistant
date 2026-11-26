# Capability Catalog and Feasibility Gate (Part G)

Status: implemented in Part G (AP-026). Decisions: D-01, D-02, D-60…D-66; deviations DV-034…DV-037.

## What it does

After the BRD Analyst, the run checks every technology, integration, login method and deployment target in
the analysis against the **Capability Catalog**. Then it records a report and pauses at the
`feasibility_decision` gate. Unsupported items block the requirements they affect, and alternatives are
offered but never applied without an approval. The coverage formula and a recommendation are shown.

```text
BRD Analyst output (validated, stored)
   │
   ▼  feasibility node (service:feasibility_gate — no model, no network, no sandbox)
effective catalog ── capabilities.yaml ⊕ CatalogKey enum ⊕ starter kits ⊕ RECIPES ⊕ latest verification
   │   map each subject → key (aliases per category; model-proposed key only if a valid CatalogKey)
   │   status (§ rules) → propagate to requirements → coverage → append-only records → FEASIBILITY_REPORT.md
   ▼
feasibility_decision gate ── proceed (acknowledgement below the threshold; disabled at "nothing to build")
   │                        └ cancel (reason cancel | revise → new run with the revised BRD)
   ▼
requirements_finalizer ── blocked requirements leave the scope (kept as "blocked_by_feasibility")
```

## Components

| Component | Location |
|---|---|
| Contracts | `devteam_core.contracts.catalog` (`CatalogKey`, `CapabilityEntry`, `CapabilityCatalog`, statuses), `contracts.records` (`FeasibilityItem` v2, `FeasibilityGap`, `TechnologyNeed`/`Integration` v2), `contracts.feasibility` (`FeasibilityReport`, `RequirementEffect`) |
| Catalog file | `capability_catalog/capabilities.yaml` + generated `schemas/capabilities.schema.json` |
| Catalog service | `devteam_services.catalog`: load and validate (fail closed), effective status, demotion audit |
| Feasibility service | `devteam_services.feasibility`: `mapping`, `rules`, `render`, `FeasibilityGate` (evaluate, verified substitutions, `apply_substitution`) |
| Records | migration `0010_feasibility`: `feasibility_reports`, `feasibility_items`, `feasibility_requirement_effects` (append-only), `approvals.subject_ref` |
| Graph | `Nodes.feasibility` (idempotent through the effect ledger), gate payload `report_id`, `_proceed_problem`, finalizer scope |
| Commands / API | `submit_gate_decision` v2 (`acknowledge_low_coverage`, `reason`), `accept_substitution`; `GET …/runs/{run_id}/feasibility`, `…/feasibility/report.md`, `GET /api/settings/catalog` |
| UI | Feasibility page (`/projects/:id/runs/:runId/feasibility`), Capability Catalog section in Settings |

## Effective status (honest demotion, AC-G-02)

A declared-supported entry is **available** only if one of its starter kits is installed with a valid
manifest, all its recipes are registered, and the kit's newest *finished* verification passed and is
*current*: the same kit checksum and locked image IDs as now. Otherwise it is **demoted**, with the reason.
Entries whose assets arrive later are demoted with "arrives in Part K/P" (D-62). Effective status is
recomputed at worker start (demotions are audited once per change) and at every evaluation. A report
records the catalog hash and the verification IDs it relied on.

## Status rules, propagation and coverage

The rules are in the `devteam_services.feasibility.rules` docstring.

- **Status per subject.** Available → supported (partial if the requested version is outside the kit's).
  Limited → partial. Unsupported, later or demoted → substitution offered if an alternative is available,
  else not supported. Unknown wording → not supported, with a gap. "SQL" → SQLite offered, with a gap (D-64).
- **Propagation (D-60).** A subject affects its explicit `requirement_ids`. Without links, a
  whole-application layer affects every in-scope FR and BR. An unlinked integration or login need is
  treated conservatively the same way, with a gap. A business rule follows its FRs. The worst effect wins.
- **Coverage (D-02).** Buildable FR+BR ÷ in-scope FR+BR. A pending substitution is not buildable; partial
  is listed separately. **Recommendation (D-63 + DV-034).**
  - *Proceed* when coverage is at or above the threshold (default 50%).
  - *Stop recommended* below the threshold, including 0% while substitutions are pending. Proceeding needs
    an acknowledgement.
  - *Nothing to build* when nothing is buildable or pending. Proceeding is disabled.

## Security

- **No model, network, sandbox or vector store.** An import-linter contract and an AST scan (with
  negative controls) enforce this.
- **Fail closed.** An invalid catalog stops the worker and the API at start-up. A missing or stale
  verification demotes the entry; it never fails open.
- **Closed enum.** A model-proposed key that is not a `CatalogKey` of the right category is rejected and
  audited (`catalog.key_rejected`). It is never added.
- **Substitutions (AC-G-04).** A substitution is applied only through a `substitution` approval whose
  `subject_ref` is `substitution:<item_id>@<item_checksum>`. The Approval Service re-verifies it, with the
  recorder evidence. A forged, rejected, other-run, other-item or changed-item approval is refused and
  audited.
- **Append-only records.** Item checksums are verified when the records are read. The API re-renders the
  report from the records and refuses to serve it if the result does not match the stored checksum.
- **Escaping.** Names from the BRD are escaped in one pass, so they can never become Markdown or HTML.

## Known limitation

The Part D BRD Analyst extracts no technology needs; that arrives in Part H. A real run's report therefore
says **"No technology needs identified"**. The Power Apps, Angular, SQL Server, "SQL", PostgreSQL and mixed
scenarios are proven on validated fixture analyses in tests, and with
`scripts/evaluate_feasibility_fixture.py`. The script writes a report labelled as a fixture evaluation and
records nothing (D-65).
