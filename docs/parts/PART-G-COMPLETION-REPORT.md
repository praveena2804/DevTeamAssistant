# Part G — Completion Report

Capability Catalog & Feasibility Gate · Gate **G-G** · Date: 2026-11-26 (final review 2026-11-27)

| Field | Value |
|---|---|
| Status | **Complete; final G-G acceptance review passed (VR-021); awaiting the owner's G-G decision.** Branch `part/g-catalog-feasibility`; not merged into `main`, not tagged, no version change (D-66 (b)) |
| Branch | `part/g-catalog-feasibility`, from `part/f-gateway-rag` at `7e9e18f` (Part F approved, AP-025); `main` unchanged at `efa5031` (v0.5.0) |
| Commits | see §10 |
| Rulings applied | AP-026: plan revision 1 approved; D-01, D-02, D-60…D-65 (a); D-66 (b). AP-027: DV-034 and DV-037 accepted |
| Plan | `docs/parts/PART-G-PLAN.md` revision 1 |

## 1. What Part G delivers

1. **Capability Catalog** (`capability_catalog/capabilities.yaml`, 32 entries, every Spec §13.1 field):
   - a closed `CatalogKey` enum and a generated JSON Schema;
   - validated when the worker and the API start (fail closed: schema, enum agreement, kit, recipe and
     document references, schema-file currency).
2. **Honest effective status**: a declared-supported entry is *available* only with an installed starter
   kit, registered recipes and a passing, **current** template verification. Otherwise it is *demoted*,
   with the reason:
   - never verified, failed or blocked;
   - stale (kit or image changed);
   - currency cannot be confirmed;
   - invalid manifest, or an unregistered recipe;
   - "arrives in Part K/P" (D-62).

   Demotions and restorations are audited once per change.
3. **Feasibility Gate service** (`service:feasibility_gate`; no model, no network, no sandbox):
   - deterministic mapping of the extracted names (aliases within the layer's category; a model-proposed
     key is accepted only if it is a valid `CatalogKey`, otherwise rejected and audited);
   - status rules, including the version check and the "SQL" rule (D-64);
   - conflict gaps;
   - dependency propagation (D-60);
   - coverage (D-02) with the threshold and recommendation (D-63, DV-034);
   - append-only records (migration 0010);
   - `FEASIBILITY_REPORT.md` rendered from the records.
4. **Gate**: the Part D stand-in is replaced by the real node (idempotent through the effect ledger).
   - The gate offers proceed or cancel (with reason cancel or revise, D-01).
   - Proceeding is refused at "nothing to build". Below the threshold it needs a recorded
     acknowledgement, which is checked by the run command **and** re-checked by the gate.
   - Blocked requirements leave the scope before the spec is drafted.
5. **Substitutions** (AC-G-04): offered only.
   - `accept_substitution` records an approval bound to `substitution:<item_id>@<item_checksum>`.
   - `apply_substitution` applies it only after the Approval Service re-verifies it, then re-evaluates into
     a new report.
6. **API and UI**:
   - `GET …/runs/{run_id}/feasibility`;
   - `…/feasibility/report.md` (re-rendered and checksum-checked);
   - `GET /api/settings/catalog`;
   - the Feasibility page (formula, items, blocked requirements, gaps, confirmation, proceed/cancel);
   - a Capability Catalog section in Settings.

**Agents: none.** No agent, prompt or model changed. No new dependency.

## 2. Acceptance criteria

| AC | Result | Evidence |
|---|---|---|
| **AC-G-01** Power Apps deployment is marked not supported and the affected requirements are identified and blocked (Spec scenario #3, on a fixture analysis, D-65) | **Pass** | `test_feasibility_graph.py::test_power_apps_is_flagged_its_requirements_blocked_and_proceeding_is_refused` (real graph, real feasibility node, real gate: FR-001…FR-004 and BR-001 blocked, coverage 0/5, proceed refused); `test_feasibility.py::test_power_apps_deployment_is_not_supported_and_blocks_every_requirement`; `test_feasibility_gate.py::test_power_apps_report_is_persisted_rendered_and_audited`; labelled script demo (§7) |
| **AC-G-02** The catalog validates at start-up; honest demotion (missing kit, unregistered recipe, no / failed / blocked / stale verification → not effectively supported, with a reason) | **Pass** | `test_catalog.py` (65): enum ⇔ YAML both directions, inconsistent catalog, unknown references, tampered file, stale schema file, invalid YAML, every demotion cause, the newest finished result wins, a fingerprint that cannot be computed never fails open, demotions and restorations audited once; `test_runtime.py` (an invalid catalog stops the runtime); real installation: every kit-based entry demoted "never been verified" (§7) |
| **AC-G-03** Formula, numerator, denominator, threshold and recommendation shown in `FEASIBILITY_REPORT.md` and the UI | **Pass** | Golden report `tests/fixtures/feasibility/golden_report.md` (`test_the_rendering_matches_the_golden_file`, byte-stable and row-order independent); threshold edges (`test_exactly_the_threshold_proceeds_and_one_point_more_stops`, not-applicable, nothing-to-build); API `test_the_report_is_returned_with_items_effects_and_the_formula`; web `PartG.test.tsx` ("shows the formula and needs the low-coverage confirmation before proceeding") |
| **AC-G-04** No substitution is applied without an approval record bound to the item and its checksum | **Pass** | `test_feasibility_gate.py`: no approval, rejected approval, approval for another item, approval for a changed item, item that offers nothing, approval from another run, **hand-written approval row** — all refused and audited (`feasibility.substitution_refused`); a valid item-bound approval applies and re-evaluates into a new report; `test_feasibility_records.py::test_a_substitution_approval_is_bound_to_one_item_and_its_checksum`; graph `test_a_substitution_is_accepted_only_for_an_offered_item_of_this_run` |

**Final acceptance review (VR-021, AP-027):** all four criteria re-verified on the final code by the final full regression (TR-0166: 2771 passed, 1 failed). The one failure is environmental: Part F's live-gateway test was refused by the
gateway's per-model memory check (`model.ram`: less than 1,720 MB free for `qwen2.5:1.5b` at that moment, after the
Docker tests) — the safety check working as designed; rerun alone it passed (TR-0168). Result: **Pass — ready for
your G-G decision.**

## 3. Tests and results

All counts are from runs actually executed (tracker TR-0148…TR-0168).

| Suite | Command | Passed | Failed | Skipped |
|---|---|---|---|---|
| Baseline before Part G (TR-0148) | `uv run pytest -m "not llm and not docker and not slow and not acceptance"` | 2491 | 0 | 1 (KI-020) |
| Fast suite on the implementation (TR-0151) | same | 2656 | 4 → investigated (below) | 1 (KI-020) |
| Static gates (TR-0152) | ruff check / format (413 files), mypy (225 files), lint-imports (20 kept), agent count and rules, schema check (110, 0 stale), eval gate | all passed | 0 | 0 |
| Web (TR-0152) | `npm --prefix apps/web run check:api / lint / typecheck / test / build` (PowerShell) | 28 tests; all steps passed | 0 | 0 |
| E2E-G, real model (TR-0153) | `pytest tests/e2e/test_e2e_g_feasibility.py -m "llm and slow"` | 1 | 0 | 0 |
| Full regression A–G with coverage, before the KI fixes (TR-0157) | `uv run pytest -m "not acceptance" -q -p no:cacheprovider -rs --cov` (includes `docker`, `llm`, `slow`; 1 h 14 min) | 2763 | 0 | 1 (KI-020) |
| DV-034 regression tests (TR-0164) | `pytest packages/services/tests/test_feasibility.py -k dv_034` | 8 | 0 | 0 |
| Clean builds and gates (TR-0165) | `uv sync --locked`; `npm ci`; web check/lint/typecheck/test/build; ruff, mypy, lint-imports, agent checks, schema check, eval gate | all passed | 0 | 0 |
| **Final full regression A–G with coverage (TR-0166)** | same command, on the final code `b7a022e` (no `acceptance` tests exist before Part R) | **2771** | **1** | 1 (KI-020: symlink creation not permitted) |
| Isolated rerun of its one failure (TR-0168) | `pytest packages/orchestration/tests/test_gateway_live.py -m llm` (5,900 MB free) | 1 | 0 | 0 |

**New Part G tests: 152 Python + 4 web**, plus 1 shared regression test for KI-046.

| File | Tests |
|---|---|
| `packages/services/tests/test_catalog.py` | 65 |
| `packages/services/tests/test_feasibility.py` | 29 (8 for DV-034) |
| `packages/services/tests/test_feasibility_gate.py` | 22 |
| `tests/integration/test_feasibility_records.py` | 9 |
| `tests/integration/test_feasibility_graph.py` | 11 |
| `tests/integration/test_feasibility_api.py` | 7 |
| `tests/security/test_feasibility_security.py` | 6 |
| `tests/scripts/test_evaluate_feasibility_fixture.py` | 2 |
| `tests/e2e/test_e2e_g_feasibility.py` | 1 |
| `apps/web/src/pages/PartG.test.tsx` | 4 |
| `tests/integration/test_audit_rows_order.py` (KI-046) | 1 |

**Failures found and how each was resolved (none dismissed without evidence):**

1. Fast suite, 3 orchestration tests (`resource.low_memory`, KI-047): **root cause found and fixed** — their
   fixtures pinned the gateway's memory check but not the Budget Manager's, so they read the host's real free RAM.
   Simulating 1,000 MB free made exactly these 3 fail; after the fix they pass under the same simulation.
   Real-model tests keep the real check on purpose and genuinely need 3,072 MB free (`low_memory` profile).
2. Fast suite, `test_runs_api.py::test_gate_decisions_go_through_the_approval_service`: it asserted the Part D
   stand-in note. Adapted (DV-035); it now also proves that deciding without a report is refused.
3. Web: a double-encoded em dash in the new test's expected text (from an earlier PowerShell write). Fixed.
   Separately (KI-048, **fixed**): Vitest failed whenever it was started from a working directory spelled with a
   lower-case drive letter (`c:\…`); the `test` script now starts it with a normalised directory
   (`apps/web/scripts/vitest.mjs`), and a planted failing test still fails through it.
4. One failure of `test_runs_api.py::test_a_refused_decision_is_a_403_and_is_audited` (KI-046): **root cause
   found and fixed** — the test helper ordered audit events by time, and this machine's clock ticks every 1–16 ms,
   so two events could share a timestamp and swap (measured: 1 misordering in 5,000 pairs). It now orders by the
   audit chain's sequence; a new test proves it, with a negative control.
5. **Defect found by the G-G demo:** a comma inside an unquoted YAML list item split nine catalog limitation texts,
   so reasons were cut short ("…is not supported: a proprietary"). Fixed in `4a752c3`, with a regression test that
   fails against the old file.
6. Full regression attempt 1 (TR-0156) was **invalid**: the lid was closed on battery, the laptop entered Modern
   Standby and the WSL/Docker VM restarted, so pytest hung; it was stopped. Its two failures came earlier: the Part D
   real-model run asserted every service step was a stand-in, which the now-real feasibility step breaks (adapted,
   DV-035, `24512cd`; passed alone in 325 s); the restart test passed when rerun alone. Attempt 2 passed in full.

7. Final regression (TR-0166), Part F's live-gateway test: refused by the gateway's per-model memory check
   (`model.ram`; the 1.5B model needs 1,720 MB free and the host had less after the Docker tests). This is the
   protection working as designed, not a defect; it passed when rerun alone (TR-0168). Part G does not touch the
   gateway.

**Coverage (final, TR-0166):** total **94%**; catalog service 98.3%, feasibility service 97.4%, catalog and
feasibility contracts 94.9%, all contracts 96.0%, harness 94.1%, Approval Service (HC-13) 97.5%, feasibility
repository 95.3%, feasibility router 97.6%, `commands.py` 96.1%, `graph.py` 86.2%. Every CLAUDE.md target
(≥ 90% harness, contracts and safety-critical services; ≥ 80% elsewhere) is met. The earlier run (TR-0157) gave
the same total.

## 4. Security and supply chain

| Check | Result |
|---|---|
| Model/network/sandbox isolation of the catalog and the gate | import-linter contract (20 kept) + AST scan `test_feasibility_security.py` with negative controls |
| API security matrix | the feasibility router is in the route inventory of `tests/security/test_api_security.py` (Part B/C matrix applied to every route); plus `test_another_projects_report_is_not_found`, `test_reading_the_report_never_writes`, `test_unknown_decision_fields_are_refused` |
| Report tampering | the download is re-rendered from the records and refused on an item-checksum mismatch (`feasibility.checksum_mismatch`) |
| Model-proposed catalog keys | accepted only as a valid `CatalogKey` of the right category; otherwise rejected and audited (`catalog.key_rejected`) |
| Hostile names in the report | escaped in one pass (`test_hostile_names_are_escaped`) |
| `pip-audit --strict` (CI command, 160 locked packages, the 4 accepted IDs) | "No known vulnerabilities found, 5 ignored" |
| `pip-audit` without ignores | only chromadb 1.5.9: PYSEC-2026-311 (×2), -3813, -3814, -3815, still **no fixed version** (KI-045 unchanged) |
| `npm audit` | 0 vulnerabilities |
| Gitleaks (`detect`, `dir .`, `protect --staged` before the commit) | no leaks |
| Dependencies | none added or changed (`uv.lock`, `package-lock.json` untouched) |
| Final audits (TR-0167) | pip-audit: "No known vulnerabilities found, 5 ignored" (160 packages) (raw: only chromadb 1.5.9 PYSEC-2026-311 (×2), -3813, -3814, -3815, no fixed version (KI-045 unchanged)); npm audit: 0 vulnerabilities; gitleaks: no leaks |
| Licences | Python: `tests/contract/test_third_party_licences.py` and the sandbox licence policy pass; web lockfile: 293 packages, all permissive except the `lightningcss` family (MPL-2.0, KI-037); no GPL/AGPL |

## 5. Decisions and deviations

- **Ruled (AP-026):** D-01, D-02, D-60, D-61, D-62, D-63, D-64, D-65, D-66.
- **Ruled (AP-027):** DV-034 **accepted** (verified by regression tests with a negative control, TR-0164); DV-037
  **accepted** (reviewed: no refinement weakens a requirement or a security control, VR-021).
- **Deviations (history):**
  - **DV-034:** "nothing to build" requires no pending substitution either. 0% with pending substitutions
    is "stop recommended", so a substitution can still be accepted in Part I's form.
  - **DV-035:** earlier tests were adapted to the real feasibility node and to item-bound approvals.
  - **DV-036:** G.1–G.5 implementation in one commit.
  - **DV-037:** design details:
    - aliases are unique within a category;
    - versions are compared as major or major.minor prefixes;
    - feasibility gaps carry no criticality (decided in Part I);
    - the acknowledgement and the reason are stored as audit events;
    - the Catalog is a section of the Settings page.
  - **DV-038:** shared test infrastructure changed to fix KI-046/047/048 (audit order helper, memory-probe fixture,
    web test launcher); no assertion weakened, each with a negative control.

## 6. Database

- **Migration `0010_feasibility`** (one revision, upgrade and downgrade): tables `feasibility_reports`,
  `feasibility_items`, `feasibility_effects` with append-only triggers; column `approvals.subject_ref`.
- **Tests:** `test_0010_upgrades_and_downgrades_cleanly`, `test_feasibility_records_are_append_only`, item checksums
  verified on read, inconsistent records refused, v1 technology needs read as v2; head revision 0010 in
  `test_migrations.py` and `apps/api/tests/test_system.py`.
- **Rehearsal on a copy of the real `data/state.db`** (TR-0150; source opened read-only, copied with the SQLite
  backup API): 0004 → 0010 → 0009 → 0010. Row counts of every pre-existing table unchanged; audit chain intact at
  each step; feasibility tables removed by the downgrade and restored; the real file byte-identical afterwards.
- The real `data/state.db` was **not** migrated (still at 0004, KI-034); the API backs it up and migrates it at its
  next start, as before.

## 7. G-G demonstration

1. **Power Apps flagged, affected requirements identified.**
   - Labelled script `scripts/evaluate_feasibility_fixture.py tests/fixtures/feasibility/power_apps.json`. The report
     opens with "FIXTURE EVALUATION — not a run" and says nothing was stored.
   - Result: Power Apps (deployment platform) → **not supported**: "a proprietary, licensed platform; the platform
     delivers source code through GitHub instead (once available, Part P)".
   - Affected: all in-scope requirements (5). **Blocked:** BR-001, FR-001, FR-002, FR-003, FR-004.
   - Coverage `0 / 5 = 0.0%` (threshold 50%), recommendation **Nothing can be built**, proceed disabled.
   - The same scenario through the real graph and gate is `test_power_apps_is_flagged_its_requirements_blocked_and_proceeding_is_refused`.
   - This demo found the truncated reason fixed in `4a752c3`.
2. **Catalog demotion.**
   - On this installation no starter kit has a passing verification, so the same report lists `html_css_js`,
     `react_vite`, `fastapi_python` and `sqlite` as demoted: "starter kit … has never been verified".
   - PostgreSQL is demoted with "arrives in Part K" (`test_later_part_assets_are_demoted_with_the_part_that_delivers_them`).
   - Every other cause is shown by `test_catalog.py`, and the Settings page lists the reason per entry.
3. **Feasibility decision recorded in a real run** (E2E-G, TR-0153).
   - A real `devteam_worker` process ran the real Part D BRD Analyst through the gateway and local Ollama.
   - The real feasibility node stored and rendered the report. The run paused at `feasibility_decision`.
   - The decision was posted through the API, recorded as an approval (`approval.recorded`,
     `feasibility_decision`) and consumed by the worker.
   - Audit: exactly one `feasibility.evaluated` by `service:feasibility_gate`. No outbound connection.
   - Honest limitation: the Part D analyst extracts no technologies, so that report says
     "No technology needs identified" (Part H).

## 8. Known issues and limitations

| ID | Item |
|---|---|
| — | The Part D BRD Analyst extracts no technology needs (Part H). A real run's report honestly says "No technology needs identified", so in real runs nothing is blocked *because nothing was identified*. The non-trivial scenarios are proven on validated fixture analyses (D-65) |
| — | On this machine the starter kits have no passing verification in the real `data/state.db` (still at revision 0004, KI-034), so every kit-based entry shows as **demoted** until the self-tests are run (D-49). This is the intended honest behaviour (AC-G-02) |
| — | Substitution acceptance has no UI before Part I (the service-level path and its approval binding are built and tested) |
| KI-045 | ChromaDB advisories (accepted, in-process only): re-checked 2026-11-26: same 4 IDs, still no fixed version |
| KI-043 | Laptop standby can disturb long real-model tests; runs used a keep-awake request |
| KI-046 | **Closed** — root cause proven (coarse Windows clock + time-ordered test helper) and fixed |
| KI-047 | **Closed** — fixture gap fixed. Remaining genuine requirement (not a defect): real-model tests need 3,072 MB free on the `low_memory` profile, and the gateway needs the model's peak RAM + 512 MB (1,720 MB for `qwen2.5:1.5b`) before loading it; after the Docker tests the WSL VM can hold enough memory to trigger this (TR-0166; mitigation: owner item KI-039) |
| KI-048 | **Closed** — Vitest now starts correctly from any working-directory spelling |
| KI-049 | **Open (outside Part G):** the Part B audit *listing* can show two events of the same clock tick in swapped order; display only, no decision uses it |

## 9. Parts A–F remain intact

- The final full regression (TR-0166) ran every test except `acceptance`, **including `docker`, `llm` and `slow`**:
  2771 passed, 1 failed (environmental, item 7 in §3; passed on rerun), 1 skipped (KI-020).
- Earlier parts' tests changed only where Part G's real behaviour required it (DV-035), keeping the same
  assertions otherwise:
  - Part D `test_graph.py`: the real feasibility node needs a stored analysis; the real-model run now expects the
    real feasibility step; item-bound substitution approvals;
  - `test_runs_api.py`: the new gate note; a report is needed before deciding;
  - `tests/fixtures/orchestration.py`;
  - head-revision checks (0010);
  - the contract `BUMPED` map.
- Files from earlier parts were modified only where a shared module gained Part G behaviour (runtime, worker,
  API start-up and routers, approvals, contracts, models); every one is listed in tracker CH-0131…CH-0135.
- The Part F branch and its approved commit `7e9e18f` are unchanged.

## 10. Branch, commits and push

| Item | Value |
|---|---|
| Branch | `part/g-catalog-feasibility`, created from `part/f-gateway-rag` at `7e9e18f` (D-66 (b)) |
| `4dce584` | feat(feasibility): Part G capability catalog, feasibility gate, records, API/UI (G.1-G.5) |
| `4a752c3` | fix(catalog): keep comma-containing limitations as one sentence (G-G demo finding) |
| `24512cd` | test(graph): the real-model run now expects the real feasibility step (DV-035) |
| `4c8f0bd` | docs(part-g): Part G completion report, trackers and Run 32 (awaiting G-G) |
| `e7c3363` | docs: record Part G branch push and remote verification (TR-0160) |
| `b7a022e` | fix(tests): resolve KI-046/047/048 and pin the accepted DV-034 rule |
| Final docs commit | this report's final review, trackers, Run 33 (hash and push verification in tracker TR-0169) |
| Push | normal pushes of `part/g-catalog-feasibility` only (TR-0160, TR-0169); after each, `git fetch` + `git ls-remote` showed the remote equal to local, the Part F branch unchanged at `7e9e18f`, `main` unchanged at `efa5031`, no new tags |
| Not done (by instruction) | no merge into `main` (`main` stays at `efa5031`), no tag, no release, no version change; the Part F branch is not altered; the spec `.docx` and `PROJECT_EXPLAINED.md` are not staged |

**Part H has not been started.** Waiting for your G-G decision.
