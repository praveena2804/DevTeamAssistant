# Part G — Plan: Capability Catalog & Feasibility Gate

| Field | Value |
|---|---|
| Status | **Revision 1, approved** (AP-026, 2026-11-26; D-01, D-02, D-60…D-65 (a), D-66 (b)) |
| Date | 2026-11-26 |
| Milestone | M2 Discovery (G, H, I). M1 complete: Parts C–F approved (Part F: AP-025) |
| Baseline | `part/f-gateway-rag` = `b0db053` (Part F approved, pushed; **not merged into `main`**, which stays at `efa5031` / v0.5.0, by owner instruction) |
| Sources | Spec §2.2, §3.4, §5.3, §5.4, §5.4.1, §5.4.3, §6.3, §7.1, §8.1, §11.1, §12, §13, §16 (FR-007…FR-011, FR-013), §17 (NFR-010, NFR-012), §20.1, §21, §23 (scenario #3); Roadmap §6.5, §12 (HC-01, HC-13), §13 Part G (and Parts H, I, K, P for boundaries), §14, §20 (D-01, D-02); Working Plan §20 Part G, HS-25; ADR-0002, ADR-0003; decisions D-04, D-16, D-24, D-38, D-49, D-55 |
| Branch (after approval) | `part/g-catalog-feasibility` (base depends on D-66, §9) |
| Agents | **None.** No agent behaviour changes; the eleventh-agent rule is untouched |

---

## 0. Summary

Part G makes the **Feasibility Gate real**. Today the graph's `feasibility` node is a Part D stand-in that
reports "not evaluated", and the `feasibility_decision` gate only records approve/decline.

1. **Capability Catalog** (`capability_catalog/capabilities.yaml` + JSON Schema + Pydantic model): every
   Spec §13.1 field, a closed `CatalogKey` enum, and validation at start-up. An entry is **effectively
   supported** only if its template exists, its recipes are registered and a passing, current
   `TemplateVerification` exists. Otherwise it is **demoted** with a recorded reason (honest demotion).
2. **Feasibility engine** (`devteam_services.feasibility`): deterministic Python, with no model and no
   network.
   - Every technology need, integration, authentication need and deployment target gets a status, a reason
     and an alternative.
   - Unsupported items block their dependent requirements.
   - Substitutions are **offered, never applied**.
   - The coverage formula (D-02) is computed and shown, with a configurable threshold (default 50%).
   - Ambiguous "SQL" wording produces a Gap (Spec §13.2).
3. **Records and report:** migration `0010` with feasibility reports, items and requirement effects
   (append-only). `FEASIBILITY_REPORT.md` is rendered from the records into the project workspace.
4. **Gate** (HC-13, D-01): the run pauses at `feasibility_decision` after the report.
   - The user can proceed with the buildable scope or cancel (cancel is also how to revise the BRD).
   - Requirements blocked by feasibility leave the scope before the spec is drafted.
   - A substitution is accepted only through a verified `substitution` approval bound to the item.
5. **UI:** a Feasibility page (rows by status, coverage formula, blockers, affected requirements,
   proceed/cancel) and a read-only **Catalog** tab in Settings, showing declared status, effective status and
   the demotion reason (D-55).

**Honest limitation:** the Part D BRD Analyst extracts no technology needs (technology extraction, FR-007,
is Part H). Until Part H, a real run's report will say **"no technology needs identified"**. Under D-38,
runs never use simulated agent output, so the Power Apps, Angular, SQL Server, "SQL", PostgreSQL and mixed
scenarios are proven on **fixture analyses in tests**. A labelled evaluation script runs the same fixtures
for the G-G demo (D-65).

---

## 1. Objectives, scope and exclusions

### 1.1 Objectives (roadmap AC-G-01…04)

| AC | Objective |
|---|---|
| AC-G-01 | Spec scenario #3 passes on a fixture analysis: a BRD requesting Power Apps deployment is marked **not supported** and the affected requirements are identified and blocked |
| AC-G-02 | The catalog validates at start-up (schema, enum agreement, references); honest demotion works (missing template / unregistered recipe / no passing or a stale verification → not effectively supported, with a reason) |
| AC-G-03 | The coverage formula, numerator, denominator, threshold and recommendation are shown in `FEASIBILITY_REPORT.md` and the UI |
| AC-G-04 | No substitution is applied without an approval record (verified `substitution` approval bound to the item and its checksum); proven by tests including a forged/missing/other-item approval |

### 1.2 In scope

- **Catalog files:**
  - `capability_catalog/capabilities.yaml`;
  - `capability_catalog/schemas/capabilities.schema.json`, generated from the Pydantic model and checked in CI like the contract schemas;
  - `capability_catalog/README.md`.
- **Contracts (`devteam_core`):**
  - `CatalogKey` (closed StrEnum);
  - `CapabilityEntry` and `CapabilityCatalog`;
  - `CatalogStatus` (declared) and `EffectiveStatus`;
  - `FeasibilityItem` **v2** and `FeasibilityReport` (D-61);
  - `TechnologyNeed` / `Integration` **v2** with optional `requirement_ids` and an optional model-proposed `catalog_key` (D-60);
  - all of these registered and exported to JSON Schema.
- **Services (`devteam_services`):**
  - `catalog`: load, validate and compute effective status from `TemplateVerification` and the recipe registry;
  - `feasibility`: mapping, status rules, dependency propagation, coverage, the "SQL" rule, substitution offers, renderer, `apply_substitution` guarded by HC-13.
- **State:**
  - migration `0010_feasibility` (upgrade and downgrade);
  - ORM rows;
  - `repositories/feasibility.py`;
  - `approvals.subject_ref` for item-bound substitution approvals.
- **Orchestration:**
  - the real `feasibility` node replaces the stand-in;
  - the `feasibility_decision` gate payload carries the report;
  - proceeding removes blocked requirements from the scope in `requirements_finalizer`, and the finalizer receives the `feasibility_item_ids`;
  - the coverage acknowledgement (D-63);
  - catalog validation in the runtime at start-up (worker and API).
- **API:**
  - `GET /api/projects/{id}/runs/{run_id}/feasibility` (report and items);
  - `GET …/feasibility/report.md`;
  - `GET /api/settings/catalog` (read-only);
  - gate decision v2 accepts `acknowledge_low_coverage`;
  - `openapi.json` regenerated.
- **Web:**
  - `FeasibilityPage`;
  - `CatalogPanel` in `SettingsPage`;
  - generated API types;
  - tests.
- **Fixtures:** TechnologyNeed sets for Power Apps, Angular, SQL Server, "SQL", PostgreSQL and mixed, plus a fully supported set.
- **Script:** `scripts/evaluate_feasibility_fixture.py` (labelled demo, D-65).
- **Docs:**
  - `docs/architecture/feasibility.md`;
  - `docs/runbooks/capability-catalog.md`, which covers how to extend the catalog: an owner change with a code enum and tests;
  - README, CHANGELOG, tracker, completion report.

### 1.3 Exclusions (and the part that owns each)

| Excluded | Owner part | Why |
|---|---|---|
| Technology extraction from BRD text (FR-007), filling `requirement_ids` / `catalog_key` on needs | H | BRD Analyst behaviour; Part G only defines and validates the fields |
| LLM mapping of ambiguous wording to a catalog key | H | Spec §5.3 allows it; Part G provides the Python validation (enum + alias table) it must pass |
| Accepting substitutions in the UI (inside the single clarification form), gap criticality, the form, the summary | I | D-01 recommendation keeps one question round; Requirements Policy Engine decides criticality |
| A deterministic BRD keyword scanner for technologies | — (not planned) | Would duplicate Part H's job; not in any source document |
| PostgreSQL template/recipes; user-provided/hosted PostgreSQL | K, Q | D-16; Spec §13.2/§14 |
| GitHub repository/Pages/Actions delivery | P | Spec §15 |
| Authentication templates beyond "no authentication" | — (D-04: `auth` slot reserved) | Not catalogued |
| Catalog editing in the UI | — | D-55 (settings read-only); catalog changes are reviewed code changes |
| Any new agent, prompt, model or dependency | — | Not needed |

### 1.4 Reconciliation notes

1. **Data model:** Spec §12 lists `FeasibilityItem(item_id, project_id, requirement_id, status, reason,
   alternative)`, and the v1 contract has `requirement_id: TraceKey`. But Spec §5.3 assigns status per
   *technology, integration, authentication requirement and deployment target*, and one such item can block
   many requirements. Proposal (D-61): v2 items have a subject (kind + ID + catalog key) and a list of affected
   requirement keys, and a separate requirement-effects table gives each requirement its status. Every
   Spec §12 field remains derivable.
2. **"Supported" before Parts K–L exist:** Spec §3.4 says supported needs "a working template, generation
   method and verification recipe". Roadmap §13 G defines the start-up check as template + registered recipes
   + passing verification. The generation adapters (developer agents) arrive in K–L. Proposal (D-62): follow
   the roadmap rule. The report says plainly that "supported" means a verified starter kit and recipes exist,
   and that code generation arrives in Parts K–L.
3. **Revise:** Spec §5.3 offers "proceed / revise / cancel". The graph has no in-run loop back to intake
   (Part D), and a second analysis round would conflict with the one-round principle. "Revise" is recorded as
   a cancellation with reason `revise`; the user uploads a revised BRD and starts a new run (D-01).
4. **Graph shape:** the separate `feasibility_decision` pause already exists (Part D topology). D-01 (a)
   needs **no topology change**, only real node behaviour and a richer gate payload.

---

## 2. Current baseline and components to reuse

| Existing (part) | Reused for |
|---|---|
| `FeasibilityStatus` (4 values), `FeasibilityItem` v1, `TechnologyNeed`, `Integration.catalog_key` placeholder, `TechnologyLayer`, `Gap`/`GapKind`/`GapCategory`, `RequirementStatus.BLOCKED` (C) | Contract base; v2 changes per D-60/D-61 |
| Graph nodes `feasibility` (stand-in) → `feasibility_decision` (gate) → `requirements_finalizer` / `finalize_run`; `GateName`/`ApprovalType.FEASIBILITY_DECISION`, `ApprovalType.SUBSTITUTION`; `config/approvals.yaml` (D) | Same topology; the stand-in entry is removed from `STAND_INS` |
| `ApprovalService.record/verify` (C/D) | Gate decision and item-bound substitution approvals |
| `TemplateManifest`, `templates/target_apps/{html,react}_fastapi_sqlite`, `RECIPES` (10 recipes), `TemplateVerification` + `latest_verifications()`, `current_fingerprint()` / staleness (E) | Effective-status computation |
| Settings router + `SettingsPage` (read-only, F) | Catalog tab |
| Agent output storage `agent_outputs` + `stored_output()` (D) | The feasibility service reads the analyst's validated output |
| Audit, tracing, JSON logging, ID helpers, clock (B/C) | `catalog.*` and `feasibility.*` audit events |
| Workspace manager (B) | `workspaces/<project_id>/reports/FEASIBILITY_REPORT.md` |

---

## 3. Proposed architecture

```text
BRD Analyst output (agent_outputs; validated BrdAnalystOutput)
        │ technology_needs, integrations, requirements, business_rules
        ▼
feasibility node (service principal service:feasibility_gate; Stage.FEASIBILITY; no model, no network)
  1. load catalog snapshot  ── CatalogService: capabilities.yaml ⊕ schema ⊕ CatalogKey enum
  │                             ⊕ template manifests ⊕ RECIPES ⊕ latest TemplateVerification (fingerprint)
  │                             → effective status per key (+ demotion reasons)
  2. map each subject → catalog key   (alias table; model-proposed key accepted only if in CatalogKey)
  3. status per subject                (rules §6.3; "SQL" rule §6.5)
  4. propagate to requirements         (links; whole-app layer rule; business rules follow their FRs)
  5. coverage                          (D-02 formula; threshold D-63)
  6. persist report + items + effects  (append-only; checksum)  → audit feasibility.evaluated
  7. render FEASIBILITY_REPORT.md      (from the records only)
        ▼
feasibility_decision gate (interrupt; payload = report id, coverage, recommendation)
  approve (proceed; + acknowledge_low_coverage when below threshold) │ reject/cancel (reason: cancel|revise)
        ▼
requirements_finalizer: in_scope = analysed keys − excluded − blocked-by-feasibility (reason recorded)
        later (Part I): substitution acceptance in the clarification form → apply_substitution(item, approval)
```

- The **catalog** is configuration plus code. `CatalogKey` is a closed enum in `devteam_core`. A test proves
  that the enum and the YAML keys are the same set, so neither a YAML edit alone nor a model answer can add a
  capability.
- The **service principal** is `service:feasibility_gate`, a new `ServiceName`. It is not an agent and does
  not appear in `AgentRole` or the agent registry (CLAUDE.md §4: the Feasibility Gate is a deterministic
  service).
- **Effective status is recomputed** at start-up and at every evaluation. A template verification can go
  stale between runs, and each report records the catalog snapshot it used (catalog file hash plus the
  verification IDs).

---

## 4. Implementation checkpoints

| # | Checkpoint | Contents | Exit evidence |
|---|---|---|---|
| G.1 | Catalog | Contracts (`CatalogKey`, `CapabilityEntry`, `CapabilityCatalog`, statuses), YAML + schema, `devteam_services.catalog` (load, validate, effective status, demotion audit), runtime start-up validation, `GET /api/settings/catalog`, Catalog tab | Table-driven catalog tests; demotion tests (no template / unknown recipe / no verification / failed / stale); enum ⇔ YAML test; tamper → start-up refused; AC-G-02 |
| G.2 | Records | `FeasibilityItem` v2, `FeasibilityReport`, `TechnologyNeed`/`Integration` v2 (D-60/D-61), migration `0010_feasibility` (+ `approvals.subject_ref`), repository, contract schema export | Migration up/down tests; append-only triggers; contract snapshot tests; rehearsal on a copy of `data/state.db` |
| G.3 | Engine | Mapping, status rules, propagation, coverage, "SQL" rule, substitution offers, `apply_substitution` (HC-13), renderer; fixture sets | Unit tests per rule and per fixture (Power Apps, Angular, SQL Server, "SQL", PostgreSQL, mixed, all-supported, none); coverage edge cases; AC-G-03, AC-G-04 |
| G.4 | Graph and gate | Real `feasibility` node, gate payload, decision v2 (`acknowledge_low_coverage`, reason cancel/revise), finalizer scope uses feasibility effects, `STAND_INS` entry removed | Graph integration with a **test-only** analyst double + real gate → report + interrupt → decisions; restart/resume idempotency; AC-G-01 |
| G.5 | API, UI, E2E, docs, validation | Feasibility API + page, report download, demo script, docs, E2E-G, full regression A–F, completion report | API security matrix; web tests; E2E-G; full regression; static gates; audits; `PART-G-COMPLETION-REPORT.md` |

Each checkpoint ends with its own tests and a commit. Tracker rows (CH, TR, DV) are added in the same session.

---

## 5. Technical tasks and expected files

### G.1 Catalog
- `packages/core/src/devteam_core/contracts/catalog.py` (new): `CatalogKey`, `CapabilityCategory`
  (frontend, backend, database, deployment, integration, auth), `CatalogStatus` (declared: `supported`,
  `supported_for_local_testing`, `supported_for_static_frontend`, `later_change_request`, `unsupported`),
  `EffectiveStatus` (`available`, `available_limited`, `demoted`, `unsupported`, `later_change_request`),
  `CapabilityEntry`, `CapabilityCatalog`; registered in the contract registry and exported to
  `contracts/schemas/`.
- `capability_catalog/capabilities.yaml`, `capability_catalog/schemas/capabilities.schema.json`,
  `capability_catalog/README.md` (new).
- `packages/services/src/devteam_services/catalog/__init__.py` (new): `load_catalog(root)`,
  `validate_catalog(...)` (fail closed on schema/enum/reference errors), `effective_catalog(session,
  settings)` → `CatalogSnapshot` (entries + effective status + reasons + catalog hash + verification IDs).
  Demotion emits a warning log and the `catalog.entry_demoted` audit event (once per change, not every
  start-up).
- `packages/orchestration/src/devteam_orchestration/runtime.py`: load and validate the catalog at
  `build_runtime` (an invalid catalog raises `RuntimeConfigError`, so the worker refuses to start);
  API `main.py` validates on start-up as well.
- `packages/harness/src/devteam_harness/iam/principals.py`: `ServiceName.FEASIBILITY_GATE`.
- API `routers/settings.py`: `GET /api/settings/catalog`. Web `components/CatalogPanel.tsx`, `SettingsPage.tsx`.
- `scripts/export_contract_schemas.py` covers the new contracts; CI also checks that the catalog schema is
  current.

### G.2 Records
- `contracts/records.py`: `FeasibilityItem` v2, `FeasibilityReport`, `RequirementEffect`; `TechnologyNeed`
  and `Integration` v2 (new optional fields; existing payloads stay valid).
- `packages/state/src/devteam_state/migrations/versions/0010_feasibility.py` (new):
  - `feasibility_reports`: id, project_id, run_id, analysis_task_id, catalog_hash, verification_ids JSON,
    numerator, denominator, coverage_pct, threshold_pct, recommendation, item_count, report_path,
    report_checksum, created_at;
  - `feasibility_items`: id, report_id, project_id, subject_kind, subject_id, subject_name, catalog_key,
    status, reason, alternative, requirement_ids JSON, gap JSON, item_checksum, created_at;
  - `feasibility_requirement_effects`: report_id, project_id, requirement_key, effect (`buildable`,
    `blocked`, `partial`, `pending_substitution`), cause_item_ids JSON;
  - `approvals.subject_ref` (nullable; `substitution:<item_id>@<item_checksum>`);
  - **append-only triggers** on the three feasibility tables (as for `model_calls`); downgrade removes them.
- `models/__init__.py` (rows), `repositories/feasibility.py` (new).

### G.3 Engine
- `packages/services/src/devteam_services/feasibility/__init__.py`, `mapping.py`, `rules.py`, `coverage.py`,
  `render.py` (new).
- `config/defaults.yaml` + `settings.py`: `feasibility.min_coverage_pct: 50`, `feasibility.report_dir:
  reports` (validated range 1–100).
- `tests/fixtures/feasibility/*.json` (new): validated `BrdAnalystOutput` fixtures.

### G.4 Graph and gate
- `packages/orchestration/src/devteam_orchestration/graph.py`: `feasibility` node calls the service through
  the attempt ledger (idempotent on resume); gate payload; finalizer scope; `services.py`: remove the
  `N.FEASIBILITY` stand-in.
- `packages/orchestration/src/devteam_orchestration/commands.py`: decision v2 (low-coverage
  acknowledgement, cancel/revise reason) and `accept_substitution(...)` (service-level; UI arrives in Part I).
- API `run_schemas.py` `GateDecisionIn` v2; `routers/runs.py`.

### G.5 API, UI, E2E, docs
- API `routers/feasibility.py` (new). Web `pages/FeasibilityPage.tsx`, `components/FeasibilityTable.tsx`,
  route, link from the run page when the run waits at `feasibility_decision`, `PartG.test.tsx`.
- `scripts/evaluate_feasibility_fixture.py` (D-65).
- `tests/e2e/test_e2e_g_feasibility.py`, `tests/security/test_feasibility_security.py`,
  `tests/integration/test_feasibility_graph.py`, `packages/services/tests/test_catalog.py`,
  `packages/services/tests/test_feasibility.py`.
- Docs listed in §1.2; tracker; `docs/parts/PART-G-COMPLETION-REPORT.md`.

---

## 6. Design details (the Working Plan's G [ARCH] list)

### 6.1 Catalog schema (Spec §13.1; every field)

| Field | Type / rule |
|---|---|
| `key` | `CatalogKey` (snake_case; must equal the YAML key and the enum) |
| `display_name`, `category` | text; `CapabilityCategory` |
| `status` | declared `CatalogStatus` |
| `compatible_versions` | list of version specifiers (PEP 440 / npm range text, validated syntax) |
| `templates` | list of `TemplateName`; each must exist under `templates/target_apps/` with a valid `TemplateManifest` |
| `generation_adapter` | name from a closed list (`html_frontend`, `react_frontend`, `fastapi_backend`, `sqlalchemy_sqlite`, `none`); recorded, implementations arrive in K–L (§1.4 note 2) |
| `build_recipe`, `test_recipes` | names that must exist in `RECIPES` |
| `runtime_requirements` | text list |
| `required_credentials` | list (must be empty for anything effectively available: NFR-013, Spec §3.4) |
| `network_requirement` | `none` \| `public_no_credentials` \| `credentialed` (`credentialed` ⇒ cannot be available) |
| `known_limitations` | text list (shown in the report) |
| `substitutions` | list of `CatalogKey` (each must exist, same category, effectively available to be *offered*) |
| `aliases` | lower-case phrases for deterministic mapping (unique across the catalog) |
| `documentation_ref` | repository-relative path that must exist |
| `available_from_part` | optional letter (e.g. `K`, `P`) shown when demoted for "arrives later" |

### 6.2 Initial entries and their expected effective status

| Category | Key | Declared | Expected effective (today) |
|---|---|---|---|
| frontend | `html_css_js` | supported | available **if** `html_fastapi_sqlite` verification passed and is current; else demoted |
| frontend | `react_vite` | supported | available **if** `react_fastapi_sqlite` verification passed and is current; else demoted |
| frontend | `angular`, `vue`, `power_apps_canvas` | unsupported | unsupported (substitution offers: `react_vite`, `html_css_js`) |
| backend | `fastapi_python` | supported | available if either kit is verified |
| backend | `dotnet`, `java_spring`, `power_automate` | unsupported | unsupported (offer: `fastapi_python`) |
| database | `sqlite` | supported (default) | available if a kit is verified |
| database | `postgresql_docker_local` | supported_for_local_testing | **demoted: arrives in Part K** (D-16, D-62); offer `sqlite` |
| database | `user_provided_postgresql`, `supabase_hosted` | later_change_request | later change request (Part Q); offer `sqlite` |
| database | `sql_server`, `oracle`, `azure_sql` | unsupported | unsupported; offer `sqlite` |
| deployment | `github_public_repo`, `github_actions_ci`, `github_pages` (static frontend only) | supported / supported_for_static_frontend | **demoted: delivery arrives in Part P** (D-62) |
| deployment | `power_apps`, `azure`, `aws`, `sharepoint`, `paid_hosting` | unsupported | unsupported (no substitution; GitHub delivery mentioned as the platform's option once available) |
| integration | `public_rest_no_credentials` | supported | available_limited: network policy (Spec §3.4) |
| integration | `sso`, `sap`, `payment_gateway`, `credentialed_api` | unsupported | unsupported |
| auth | `no_auth` | supported | available (D-04) |
| auth | `enterprise_identity_provider`, `local_password_rbac` | unsupported | unsupported (D-04 reserves the slot) |

### 6.3 Status rules (deterministic; Spec §5.3)

| Subject situation | Feasibility status | Reason / alternative |
|---|---|---|
| Maps to a key that is effectively available | `supported` | — |
| Maps to `available_limited` | `partial` | the limitation |
| Maps to `unsupported` / `later_change_request` / `demoted`, and an available substitution exists | `supported_with_substitution` (**offered**, `pending`) | why + the alternative key; not buildable until approved (D-02) |
| Same, but no substitution | `not_supported` | why (incl. "arrives in Part K/P" for demotions) |
| Name not found in aliases and no valid proposed key | `not_supported` | "not in the Capability Catalog"; Gap (technology, unclear) for the Finalizer |
| Model-proposed `catalog_key` not in `CatalogKey` | rejected (guardrail) | audit `catalog.key_rejected`; treated as "not found" |
| Two needs on the same layer conflict (e.g. React **and** Angular as *the* frontend) | each item evaluated; plus a Gap (conflicting, both sources) | Spec §21 "never silently choose" |
| Version requested outside `compatible_versions` | `partial` | "version X requested; Y is what the starter kit provides" |

### 6.4 Dependency propagation (D-60)

1. A need or integration with `requirement_ids` affects exactly those requirements.
2. A need on a **whole-application layer** (frontend, backend, database, deployment, language/runtime of the
   app) without links affects **all** in-scope FR and BR keys. Power Apps as the *platform* therefore blocks
   every requirement, and the reason names the item.
3. An integration or authentication need without links is treated **conservatively** as affecting all
   in-scope keys, with a Gap "affected requirements not identified". The Finalizer narrows it in Part I.
4. A business rule follows the FRs it references. Acceptance criteria follow their requirements.
5. Effect precedence per requirement: `blocked` > `pending_substitution` > `partial` > `buildable`. Every
   effect lists its cause items.

### 6.5 "SQL" rule (Spec §13.2)

- Applies when a database need names only "SQL", "relational database" or "a database" (alias class
  `ambiguous_sql`).
- It is **never** mapped to an enterprise product.
- It produces an item `supported_with_substitution` (alternative `sqlite`, pending) and a Gap
  (technology, unclear, with the evidence). The Gap reads: "Engine not specified; if accepted, SQLite is
  used and disclosed in the summary."
- An explicit "PostgreSQL" gets `postgresql_docker_local`, which is demoted until Part K, so a SQLite
  substitution is offered.
- A named paid or hosted engine (SQL Server, Oracle, Azure SQL, Supabase) is `not_supported` or a
  `later_change_request`, with the SQLite offer.

### 6.6 Coverage (D-02 recommended (a); D-63)

```text
denominator = |in-scope FR keys| + |in-scope BR keys|        (user-excluded keys left out; NFRs reported, not counted)
numerator   = |keys whose effect is buildable| + |keys whose cause substitutions are all approved|
coverage    = numerator / denominator × 100   (shown with both numbers; "n/a" when the denominator is 0)
partial     = reported separately (count + keys), never counted as buildable
recommendation = "proceed" if coverage ≥ min_coverage_pct (default 50) else "stop recommended" (report never hidden)
```

- A coverage of 0 with a non-empty denominator **disables proceed**, because there is nothing to build.
  Cancel remains available.
- Below the threshold, proceeding requires an explicit acknowledgement (D-63).

### 6.7 Report (`FEASIBILITY_REPORT.md`, rendered from the records only)

Sections:
1. Header: project, run, date, catalog hash, verification IDs.
2. Coverage formula with the numbers.
3. Recommendation.
4. Table of items by status: subject, source reference, catalog key, status, reason, alternative,
   affected requirements.
5. Blocked requirements.
6. Partial items.
7. Offered substitutions, marked "offered — not applied".
8. Gaps raised for the clarification form.
9. Catalog entries demoted at evaluation time, with reasons.
10. Plain statement of what "supported" means today (§1.4 note 2).

The renderer is plain Python (no new dependency). The output is byte-stable for the same records; a golden
test checks this.

### 6.8 Gate UX (D-01 recommended (a))

- The run waits at `feasibility_decision`, and the project page links to the Feasibility page.
- **Proceed:** the Proceed button stays disabled until a checkbox is ticked when below the threshold, and is
  hidden at 0% coverage. **Cancel** takes a reason: cancel, or revise ("upload a revised BRD and start a new
  run").
- Substitution offers are shown with "accepted in the clarification form (Part I)". The gate itself never
  applies one.
- The approval record is `feasibility_decision` (and stays unbound to a spec: `bind_spec: false`). The
  decision is audited with the report ID and coverage.

---

## 7. Security considerations and failure handling

- **No model, no network, no sandbox** in catalog or feasibility code. An import contract enforces this:
  `devteam_services.catalog` and `devteam_services.feasibility` must not import `llm_gateway`, httpx,
  docker or chromadb.
- **Closed enum guardrail (HC-01):**
  - any externally supplied catalog key (model-proposed, API input) is validated against `CatalogKey`;
  - an unknown key is rejected and audited, never added;
  - negative control: a planted extra YAML key without an enum member makes start-up fail.
- **Fail closed:**
  - an invalid catalog stops the worker and the API at start-up (schema error, duplicate alias, unknown
    recipe or template reference, enum mismatch);
  - a missing or stale verification **demotes**; it never fails open into "supported".
- **Substitution integrity (AC-G-04):** `apply_substitution` re-verifies through `ApprovalService.verify`:
  - type `substitution`, same project and run;
  - `subject_ref` equals the item ID **and** its checksum;
  - decision `approved`.

  A forged, other-run, other-item, rejected or stale (item changed) approval is refused and audited.
- **Append-only records** (triggers). A re-evaluation creates a new report; it never edits an old one.
- **Untrusted content:** names and quotes from the BRD are rendered as escaped text in Markdown and HTML.
  The report never includes raw chunk text beyond the cited quote (≤ 1,000 characters, already
  contract-limited).
- **Idempotency:** the feasibility node uses the attempt ledger. A crash after persisting returns the same
  report on resume and creates no duplicate.
- **Authorization:**
  - all new routes need the per-install token (Part B);
  - the gate decision needs `project_user` or `project_admin` (approvals.yaml);
  - the security matrix test covers the new routes.

---

## 8. Database, API and UI impact

| Area | Change |
|---|---|
| Database | Migration `0010_feasibility` (3 tables + `approvals.subject_ref`); up/down tested; rehearsal on a copy of `data/state.db` (0004 → 0010 → 0009 → 0010). The real DB is migrated with a backup at the API's next start (KI-034) |
| API | New `routers/feasibility.py`; `GET /api/settings/catalog`; `GateDecisionIn` v2 (backward compatible: new fields optional); `openapi.json` regenerated; web types regenerated (`npm run gen:api`) |
| UI | `FeasibilityPage`, `FeasibilityTable`, `CatalogPanel`; run page link; no editing of the catalog |
| Config | `config/defaults.yaml` `feasibility:` section (validated); new top-level `capability_catalog/` (Spec §8.1) |

---

## 9. Decisions requiring owner input

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **D-01** | Feasibility decision point (Spec §5.3 vs §7.1) | (a) separate pause; (b) fold into the clarification form | **(a)**, as the roadmap recommends and the Part D topology already provides. Choices are proceed and cancel; "revise" is recorded as a cancel reason followed by a new run. **Substitution acceptance moves into the single clarification form (Part I)**; Part G builds and tests the guarded `apply_substitution` path |
| **D-02** | Coverage denominator | (a) in-scope FR + BR count; (b) weighted; (c) technologies only | **(a)** (roadmap). Buildable = supported + approved substitution; partial reported separately; formula as in §6.6 |
| **D-60** (new) | How technology needs and integrations link to the requirements they affect | (a) add optional `requirement_ids` (filled by the Analyst in Part H) **plus** the whole-application layer rule and the conservative rule for unlinked integration/auth (§6.4); (b) infer from shared source chunks; (c) whole-application rule only | **(a)**: explicit and explainable. (b) is fuzzy, and (c) cannot identify the affected requirements for integrations |
| **D-61** (new) | `FeasibilityItem` shape (contract v2; differs from Spec §12's illustrative fields) | (a) one item per subject with affected requirement keys, plus a requirement-effects table; (b) one item per (subject, requirement) pair, keeping `requirement_id` | **(a)**. Recorded as a deviation with reason; every Spec §12 field remains derivable |
| **D-62** (new) | Capabilities whose assets arrive in later parts (PostgreSQL local: K; GitHub delivery: P), and the meaning of "supported" before K–L | (a) honest demotion with "arrives in Part X" (PostgreSQL → SQLite substitution offer; GitHub delivery → not available yet) and the roadmap availability rule; (b) report them as `partial`; (c) treat the declared status as effective | **(a)**: matches Spec §3.4 ("a name … without those assets is not sufficient") and the roadmap rule. (c) would break AC-G-02 |
| **D-63** (new) | Behaviour below the coverage threshold | (a) default 50%, configurable; below it "stop recommended" and proceeding needs an explicit acknowledgement; proceed disabled at 0%; (b) hard stop below the threshold; (c) warning only | **(a)**: Spec §5.3 says recommend stopping but let the user proceed with the supported scope |
| **D-64** (new) | Output of the "SQL" rule | (a) `supported_with_substitution` (SQLite, pending) **plus** a technology Gap for the one-round form; (b) `partial` plus a Gap; (c) Gap only, no item | **(a)**: Spec §13.2 ("if the user accepts the default, use SQLite and disclose it") is a substitution the user must accept |
| **D-65** (new) | How the G-G demo shows Power Apps and the other scenarios while the Part D Analyst extracts no technologies (D-38 forbids simulated agent output in runs) | (a) tests on fixture analyses, plus a labelled script that evaluates a fixture file with the real engine and catalog and writes a report to a temporary folder (no project records, no run); (b) pull technology extraction forward from Part H | **(a)**. (b) would take Part H scope |
| **D-66** (new) | Base branch for `part/g-catalog-feasibility` | (a) first merge Part F into `main` with version 0.6.0 and tags (only on your explicit instruction), then branch from `main`; (b) branch from `part/f-gateway-rag` and keep `main` at v0.5.0 for now | **(a)** if you want the usual release step, otherwise **(b)**. Either way, I will not merge or tag without your instruction |

No new dependency is proposed. Agents, prompts and models are unchanged.

---

## 10. Testing strategy and acceptance criteria per checkpoint

| Level | Tests |
|---|---|
| Unit: catalog (G.1) | Table-driven: one row per catalog entry (declared → effective); demotion causes individually (template missing, manifest invalid, recipe unknown, no verification, failed, blocked, stale fingerprint); enum ⇔ YAML both directions; duplicate alias; unknown substitution key; credentialed entry cannot be available; schema file current |
| Unit: engine (G.3) | Each rule in §6.3; propagation cases in §6.4; "SQL" variants; fixtures: Power Apps (deployment not supported; all requirements blocked; AC-G-01 logic), Angular (substitution offer to React, pending, not buildable), SQL Server (not supported + SQLite offer), "SQL" (substitution + Gap), PostgreSQL (demoted, SQLite offer, "arrives in Part K"), mixed, all-supported (100%), no needs; coverage edge cases (0 denominator → n/a; 0% → proceed disabled; exactly 50%; exclusions; partial not counted); renderer golden file; **substitution never applied without approval** (none / rejected / forged / other item / stale checksum / other run → refused) and applied with a valid one |
| Contract | `FeasibilityItem` v2, `FeasibilityReport`, `TechnologyNeed`/`Integration` v2 snapshots; old v1 payloads of needs still validate |
| State (G.2) | Migration up/down; append-only triggers (update/delete refused); repository round trip; rehearsal on a real DB copy |
| Integration: graph (G.4) | Graph with a **test-only BRD Analyst double** (validated fixture output) + the real feasibility node + real gate → report persisted + interrupt payload; proceed → finalizer scope excludes blocked keys; cancel/revise → run cancelled with reason; low coverage without acknowledgement → refused; crash between persist and gate → resume, one report |
| Security | New routes in the API matrix (auth required, project isolation: another project's report is 404); import contract (no model/network in catalog/feasibility); tamper tests; HTML/Markdown escaping of hostile names |
| E2E-G | Real API + worker + real Part D agents through the real gateway on the sample BRD: the run pauses at `feasibility_decision` with an honest report ("no technology needs identified"), and the decision is recorded and audited (`llm` + `slow`, keep-awake) |
| Web | Feasibility page (rows, formula, disabled/enabled proceed, acknowledgement), Catalog panel |
| Regression | Full suite A–F (`-m "not acceptance"` incl. `docker`, `llm`, `slow`, keep-awake), static gates, audits (gitleaks, npm audit, pip-audit with the KI-045 IDs), coverage ≥ 90% for the new services and contracts |

**G-G demonstration:**
- the Power Apps fixture flagged, with its blocked requirements (script and UI from a test project);
- a demoted catalog entry (PostgreSQL "arrives in Part K", and a kit demoted when its verification is
  stale);
- a feasibility decision recorded in a real run.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Real runs show no technology needs until Part H, which may look like "everything supported" | The report states it plainly (headline + Gap); coverage shows nothing blocked *because nothing was identified*; documented in README and the completion report |
| Template verifications stale on the owner's machine → kits demoted → every frontend "demoted" | Intended honesty (AC-G-02); runbook explains how to re-run verification (D-49 manual request); the Catalog tab shows the reason |
| Contract v2 changes ripple into Part D tests and agent outputs | Fields optional; v1 payloads remain valid (contract test); deviations recorded |
| Conservative propagation blocks too much (unlinked integration) | Reason and Gap name the item; Part I narrows it; recorded as D-60 behaviour |
| Alias table false matches (e.g. "react" in prose) | Mapping only runs on analyst-extracted need names, not on raw text; aliases are exact normalised phrases; tests for near misses |
| Windows path length for report files (KI-044) | Short fixed path `reports/FEASIBILITY_REPORT.md` |
| Standby during long tests (KI-043) | Keep-awake with display required for the full regression; event-log check recorded |

---

## 12. Git workflow and completion criteria

- **Branch:** `part/g-catalog-feasibility`, from the base chosen in D-66. Each checkpoint (G.1–G.5) gets one
  Conventional Commit with the Co-Authored-By line. The spec `.docx` and `PROJECT_EXPLAINED.md` are never
  staged.
- **Push:** only with your instruction; the part branch only. No merge, tag or release without explicit
  approval.
- **Part G is complete when:**
  - AC-G-01…04 are met;
  - the G-G demo items are shown;
  - the full regression has no unexplained failure;
  - static gates and audits are clean;
  - the tracker is updated (CH, TR, DV, KI) and the Run paragraph is written;
  - `docs/parts/PART-G-COMPLETION-REPORT.md` is written.

  Then I stop for G-G.

---

## 13. Critical review: own findings (revision 1)

1. **Risk of scope creep into Part H:** avoided. No technology extraction, no prompt change. The new contract
   fields are optional and only validated.
2. **Risk of scope creep into Part I:** avoided. No form, no criticality, no substitution UI. The guarded
   `apply_substitution` service path and its approval binding are built because AC-G-04 belongs to Part G.
3. **The spec's data-model field `requirement_id` is not kept 1:1:** raised as D-61 instead of being
   changed silently.
4. **Honesty under D-38:** demonstrations of non-trivial scenarios use fixtures in tests and a labelled
   script; runs never fake analyst output.
5. **Gate topology unchanged:** existing topology tests (Part D) stay valid; only behaviour and payload
   change.
