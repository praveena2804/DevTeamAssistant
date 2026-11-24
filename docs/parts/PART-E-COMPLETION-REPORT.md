# Part E — Completion Report

Secure Docker Sandbox and Starter Kits · Gate **G-E** · Date: 2026-11-24

| Field | Value |
|---|---|
| Status | **Approved (G-E, AP-022, 2026-11-24)** and released as `0.5.0` |
| Branch | `part/e-sandbox` (based on `main` after the G-D merge, `556fde3`), merged into `main` with `--no-ff` |
| Commits | `aab0205` E.1 · `5c06d3d` E.2 · `cc3fb9e` E.3 · `15e8f61` E.4 · `fc60c70` E.5 · release 0.5.0 and merge: see §10 |
| Source of truth | `docs/parts/PART-E-PLAN.md` revision 3 |
| Rulings applied | AP-019 (D-07 (b): Docker Engine inside WSL2, free only), AP-020 (G-D), AP-021 (Part E authorised; D-04, D-08, D-16, D-24, D-45…D-51 approved, D-50 conditional on S-1; D-52 still depends on D-21) |
| Spikes | **S-1 passed** (E.1) · **S-2 passed** (E.2) · **S-3 passed** (E.2). No blocker; no control was weakened |

## 1. Implementation summary

1. **Engine (E.1).** The free Ubuntu `docker.io` engine (29.1.3) runs inside WSL2 `Ubuntu-24.04`. It listens only on
   `127.0.0.1:2376` with mutual TLS (`tlsverify`).
   - The private CA key is deleted after issuance.
   - The client key exists only in encrypted form (PKCS#8, AES-256).
   - Its passphrase exists only in Windows Credential Manager.
   - Docker Desktop, non-Linux engines, plain TCP and non-loopback hosts are refused. There is no host fallback.
   - `scripts/setup_wsl_docker.py` sets everything up (run once by the owner as root inside WSL).
   - `scripts/doctor.py` checks engine identity, certificate expiry and the lease.
2. **Disposable containers (E.2).** One function builds every container spec. The engine's own record is
   re-inspected before start, and any difference from the required controls blocks the run. Files go in and
   out through the archive API only. Container and volume are always removed.
3. **Locked images (E.2).** `python_api` and `node_web`: base images pinned by digest, hash-pinned wheels,
   `npm ci --ignore-scripts`. They are built by the owner-run `scripts/build_sandbox_images.py`, the only step
   that uses the network, with a licence-policy check and vulnerability audits. A run refuses an image whose
   ID differs from `images.lock.json`.
4. **Resource Guard (E.1/E.2).** A SQLite cross-process lease (D-46) makes model calls and sandbox runs
   strictly one at a time. Before a run, Ollama models are unloaded and free memory is checked
   (`sandbox.min_free_ram_mb` 2,048). If not enough is free, the work *waits*; it is never forced.
5. **Build/Integration service, deterministic (E.3).**
   - **Ten fixed recipes**, argument lists only, no shell: `pytest_api`, `pytest_db`, `alembic_upgrade`,
     `alembic_autogenerate`, `ruff_check`, `bandit_scan`, `static_validation`, `tsc_check`, `vitest_run`,
     `npm_build`.
   - **Inputs** are validated and size-limited.
   - **Results** come only from parsed JUnit/coverage reports (entity and DTD declarations refused).
   - **Records:** every run is an append-only `SandboxExecution` record with audit.
   - **Execute tools:** `request_build`, `request_test_run`, `request_migration_check`, with per-role
     ceilings. No agent declares them yet (D-47), so the run graph is **not** wired to the sandbox.
   - **Services:** Migration Runner, Test Runner and Integration Builder.
6. **Starter kits (E.4).** `react_fastapi_sqlite` and `html_fastapi_sqlite` (shared FastAPI + SQLAlchemy +
   Alembic + SQLite backend) each have:
   - a manifest, protected-file checksums and slots for generated code;
   - dependency versions equal to the locked image versions;
   - their own self-test.

   Verification records move from queued to passed, failed, blocked or stale (stale when the kit or an image
   changes).
7. **Integration (E.5).**
   - **Worker:** runs queued verifications, writes `data/sandbox_status.json` and sweeps leftover containers at
     start.
   - **LLM gateway:** holds the shared lease.
   - **API:** `GET /api/sandbox/status`, `GET /api/sandbox/templates`,
     `POST /api/sandbox/templates/{id}/verifications`, `GET /api/sandbox/verifications/{id}`. The API never
     contacts Docker; an import contract enforces this.
   - **Web:** a **Sandbox** page.

## 2. Files created and modified

Full list with exact locations: `docs/IMPLEMENTATION_TRACKER.md` §5/§6, CH-0115…CH-0119.

| Area | Created | Modified |
|---|---|---|
| Harness | `sandbox/{__init__,errors,tls,engine,spec,runner,guard,images,licences,recipes,results,service,tools}.py`, `budgets/resource_lease.py` | `tools/{registry,builtin,layer}.py`, `guardrails/config.py`, `llm_gateway/gateway.py`, `llm_gateway/providers/ollama.py` (DV-025, DV-028) |
| Core / state | `contracts/sandbox.py` (9 contracts), migration `0007_sandbox`, `repositories/sandbox.py` | `config/settings.py` (sandbox settings), `config/defaults.yaml`, profiles, models |
| Services | `migration_runner`, `test_runner`, `integration_builder`, `templates` | — |
| Orchestration / worker / API / web | `routers/sandbox.py`, `SandboxPage.tsx`, `PartE.test.tsx` | `runtime.py`, `worker.py`, worker `main.py`, API `main.py`, `openapi.json`, `schema.d.ts`, `client.ts`, `App.tsx`, `Layout.tsx` (DV-028) |
| Images and kits | `sandbox/images/**`, `sandbox/tools/**`, `templates/target_apps/**` | — |
| Scripts | `setup_wsl_docker.py`, `build_sandbox_images.py` | `doctor.py`, `dev.ps1` |
| Config / policy | — | `config/guardrails/policies.yaml`, `pyproject.toml` (import contracts, lint), `agents/ruff.toml`, `.gitleaks.toml`, `uv.lock` |
| Tests | `tests/sandbox/*` (S-1, S-2, S-3, isolation, recipes, template verification, E2E-3, Ollama guard), `tests/integration/test_starter_kits.py`, `test_sandbox_api.py`, `test_template_verification_units.py`, `tests/security/test_sandbox_failure_paths.py` and other security and contract tests; worker tests +2 | `test_tool_layer.py` (DV-025), `test_audit_chain.py` (DV-027), `test_api_security.py`, doctor test (DV-023), contract samples |
| Docs | ADR-0008, `docs/architecture/sandbox.md`, `docs/runbooks/docker-setup.md`, this report | README, CHANGELOG, `THIRD_PARTY_LICENSES.md`, both trackers |

## 3. Security controls

| Requirement | Control | Evidence |
|---|---|---|
| No network from sandbox containers | `network_mode=none`, network disabled; re-checked on the created container | Isolation: outbound TCP and DNS fail; bridge negative control succeeds |
| No secrets in logs, traces, test reports | Passphrase registered with the redactor; only booleans compared in tests; recipe environment is declared, never inherited | S-1 #4/#5; host-canary test; E2E-3 checks the worker log and API responses |
| No plaintext private key storage | Key generated encrypted; decrypted only in memory | S-1 #3/#5: no plaintext-key marker in `data/`, the repository or `%TEMP%` |
| TLS verification never disabled | `CERT_REQUIRED`, hostname check, TLS ≥ 1.2, private CA only; weakened contexts refused | S-1 #6 + static AST scan |
| No host filesystem exposure | No bind mounts, never the Docker socket; per-run volume; archive copy | S-3; `locked_down_problems`; host-path test with negative control |
| No arbitrary agent commands | Ten fixed recipes; argv only; no `npm`/`npx`/`pip` at run time; execute tools refused to agents without a grant | Recipe validation tests; Tool Layer refusals; no agent declares the tools |
| No container reuse | New container + volume per execution, removed in `finally`; startup sweep by label | Cleanup tests after success, failure, timeout and cancel; sweep test |
| Fail closed | Engine unreachable, TLS missing, Docker Desktop, image mismatch, control mismatch → **Blocked**; low memory → wait | Docker-absent test; S-3 #5 (`wsl --shutdown` → Blocked); E.4 run 1 recorded BLOCKED |
| Unexecuted tests never "passed" | `completed` needs exit 0; passed needs every recipe successful and test recipes with passed > 0 and no failures/errors | Broken-kit test recorded FAILED; contract tests |
| Non-root, read-only, limits | User 10001, `cap_drop ALL`, `no-new-privileges`, read-only root, `noexec` tmpfs, memory = swap, CPU, PIDs, wall clock | Isolation suite, each with a negative control |
| No host execution (AC-E-04) | Static scan of all non-test platform code for `subprocess`/`os.system`/`exec`/`spawn`/`pty`; import contract: only `sandbox.engine` imports the SDK | Static-rule tests with planted violations |

## 4. Tests and exact results

| Run | Command | Result |
|---|---|---|
| Full regression, first E.5 run (all markers except `acceptance`, with coverage; TR-0126) | `uv run pytest -m "not acceptance" --cov` | 4 failed, 2,444 passed, 1 skipped (65 min). The failures found two real defects: **KI-040**, the worker crashed at start without a `templates/` folder (it broke Part D's E2E-2), and **KI-041**, the S-3 test stopped WSL and left later tests with no engine (they were correctly recorded BLOCKED). Both were fixed with tests and re-verified |
| After the fixes (TR-0129, TR-0130) | E2E-2 with the real model; S-3 #5 followed by the kit tests; new failure-path tests | E2E-2 **1 passed** (7 min 25 s); S-3 → kits **5 passed**; 27 + 6 + 3 new tests passed; negative control for KI-040: 3 failed without the fix |
| Fast suite (TR-0127) | `pytest -m "not llm and not docker and not slow and not acceptance"` | **2,385 passed, 1 skipped** (KI-020: symlinks not permitted on this machine), 99 deselected |
| Static gates (TR-0127) | `ruff check .`, `ruff format --check .`, `mypy packages apps scripts`, `lint-imports`, agent count/rules, contract schemas, OpenAPI + `check:api` | All clean: mypy 189 files; 15 import contracts kept; 101 schemas, 0 stale |
| Web | `npm --prefix apps/web run lint / typecheck / test / build` | lint and typecheck clean; 22 passed (5 files); build OK |
| E.3 recipes (real engine) | `pytest tests/sandbox/test_recipes_tools.py -m docker` | 33 passed |
| E.4 kits (real engine) | `pytest tests/sandbox/test_template_verification.py -m docker` | Run 1: 1 failed, 3 passed (html kit **BLOCKED** for low memory while other work ran: the guard worked as designed). Run 2: **4 passed** |
| E2E-3 (real API + real worker process + real engine, `low_memory`) | `pytest tests/sandbox/test_e2e_3_sandbox.py -m docker` | **1 passed** (141 s) |

Coverage (first E.5 run): 92.4% total (branch). Some Part E failure branches were below target there, so I
added failure-path tests (hostile reports, engine errors, evidence paths, verification outcomes) and removed
one unused function. The final run on `main` measured the result.

### Final verification on `main` (TR-0131)

| Check | Result |
|---|---|
| Clean install from lock files | `uv sync --locked --all-packages` OK; `npm ci` OK, 0 vulnerabilities |
| Web | lint and typecheck clean; 22 passed; build OK |
| Full regression, every marker except `acceptance` (63 min) | **2 failed, 2,482 passed, 1 skipped** (KI-020) |
| The 2 failures | Both real-model graph tests stopped with *waiting for resources* (834 / 763 MB free; the executor needs 1,024 MB). The memory guard worked; nothing ran unsafely |
| Re-runs of those two tests alone | Two attempts without a keep-awake request were disturbed by the laptop's Modern Standby (Windows events 14:16–14:51 and 15:26–15:48): one full run blocked, and one restart run counted 8 model calls instead of 7 because a call timed out during standby and was retried as designed (KI-043). One diagnostic attempt hit the Windows 260-character path limit because of a long test folder (KI-044). With a short folder the full-run test passed (6 min 8 s). Final re-run of both **with** keep-awake: **2 passed** (10 min 18 s) |
| Coverage | **92.6%** total. Part E harness modules 90.5–100% (`engine.py`, `results.py`, `images.py` 100%); services 93.3–100%; sandbox API 98.4%. `worker.py` 66.4% in that run (its loops run in subprocesses); 3 in-process worker tests added, 82% from the worker tests alone |

## 5. Adversarial results

Every isolation test has a **negative control**: the same probe in a container with exactly that one control
removed must succeed, proving that the test detects the control.

| Attack | Result | Negative control |
|---|---|---|
| Outbound TCP / DNS | Fails | Bridge network: succeeds |
| Write to root filesystem | Fails | Writable root: succeeds |
| Run as root, use capabilities | uid 10001, no capabilities | Root user: succeeds |
| Execute from `/tmp` | Refused (`noexec`) | `exec` tmpfs: succeeds |
| Memory bomb | Killed, OOM recorded | Enough memory: completes |
| Fork bomb | Stopped by the PID limit | Higher limit: more processes |
| Endless loop | Killed at the wall clock, recorded as timeout | — |
| Read host paths, home, environment canary | Not visible | — |
| Forged JUnit report (entity declaration) | Rejected | — |
| Output name escaping the output folder (`out/../../etc/x`) | Never collected | — |
| Altered image | Refused (digest mismatch) | — |
| Client cert from a foreign CA; no client cert; untrusted server | Refused / `SSLCertVerificationError` | Own certificate: works |
| Docker Desktop or plain TCP endpoint | Refused at config load / connect | — |
| Agent calls an execute tool without a grant, or at the wrong stage | Refused by the Tool Layer and audited | Granted role in the right stage: runs |
| PostgreSQL migration check | Refused (D-16: deferred to Part K) | — |

## 6. Dependency and licence audit

- **New platform packages:** `docker` 7.2.0 (Apache-2.0) and `pywin32` 312 (PSF-2.0); inventory in
  `docs/THIRD_PARTY_LICENSES.md`. No paid service, account, API or subscription anywhere (NFR-013).
- **Platform `pip-audit --strict`:** no known vulnerabilities (78 runtime packages; 109 including dev
  groups). `npm audit` for the web app: 0 vulnerabilities.
- **Images:** licence policy check at every build. `python_api` 123 packages (tier 1: 87, tier 2: 1, tier 3: 18,
  tier 4: 17); `node_web` 171 (88 / 0 / 80 / 3). No refused or unknown licence. `pip-audit` on the image lock
  is clean (35 checked) and `npm audit` is clean.
- **Owner review items (KI-037):** tier-3 MPL-2.0 packages `lightningcss` (+ 2 platform binaries, a Vite 8
  dependency) and `certifi` (as KI-026). Unmodified, separate, used only inside the sandbox image.
- **Secrets:** `gitleaks detect` (whole history) and `gitleaks dir .` (working tree): no leaks. The only allow-list entry is the *encrypted* client key file
  `data/sandbox_tls/client-key.enc.pem` (which is in `data/` and never committed).

## 7. Container and offline-build verification

| Item | Value |
|---|---|
| Engine | Ubuntu `docker.io` 29.1.3, Linux, `Ubuntu-24.04`; API pinned to 1.44; never Docker Desktop |
| Images | `python_api` `sha256:4e10191d7f74…`, `node_web` `sha256:95620d56d3ae…` (`sandbox/images/images.lock.json`) |
| Offline | Every container runs with `network=none`. React: `tsc`, Vite build and Vitest use the image's `/node_modules`; no download; `docker diff` shows the image unchanged (S-2) |
| Kit results (E.4, `low_memory` limits) | `html_fastapi_sqlite` **PASSED**: 49.1 s, peak 98.4 MB (6 recipes; pytest 3 + 2 passed). `react_fastapi_sqlite` **PASSED**: 65.3 s, peak 278.3 MB (9 recipes; pytest 3 + 2, Vitest 3 passed; `tsc_check` highest) |
| E2E-3 (AC-E-03) | Through the API and a real worker under `low_memory`: html 54.2 s / 98.3 MB, React 66.1 s / 278.3 MB; every container's limit 1,536 MB |

## 8. Database migration and backup status

- Migration `0007_sandbox` adds `template_verifications` (forward-only status, frozen when final),
  `sandbox_executions` (append-only) and `resource_lock` (single row). Upgrade and downgrade are tested.
- Rehearsal on a copy of the real `data/state.db` (read-only source, SQLite backup API), 2026-11-24:
  0004 → 0007 → 0006 → 0007. Row counts unchanged, audit chain intact at each step, sandbox tables removed by
  the downgrade, **real file byte-identical**.
- **The real database is still at revision 0004** (KI-034). The API backs it up automatically and migrates it at
  its next start (`scripts/dev.ps1`), as in Part A.

## 9. Known issues and limitations

| ID | Item |
|---|---|
| KI-037 | Tier-3 MPL-2.0 packages (`lightningcss`, `certifi`) await your review |
| KI-038 | WSL stops an idle distribution; `scripts/dev.ps1` and the tests keep one idle session open. Without it, sandbox work is Blocked (never run elsewhere) |
| KI-039 | WSL keeps file cache and returns memory to Windows slowly, so the Resource Guard may make sandbox work wait. Recommended: `autoMemoryReclaim=gradual` in `%USERPROFILE%\.wslconfig` (owner-managed, D-45; the platform never writes it). The threshold is not lowered. On this laptop, open browsers plus test runs also brought free memory below 2 GB once (E.4 run 1, recorded BLOCKED) |
| KI-034 | Real database at revision 0004 until the next API start |
| KI-040 | **Fixed.** The worker crashed at start when its root had no `templates/` folder (found by Part D's E2E-2) |
| KI-041 | **Fixed.** The S-3 test stopped WSL and left later tests without an engine; its duration variable was rejected by the strict configuration |
| KI-042 | `mypy tests` (not a defined gate) reports 35 type errors in 9 older test files; none in Part E code |
| KI-043 | Real-model tests run without a keep-awake request are disturbed when the laptop enters Modern Standby (a model call times out and is retried; the restart test's exact call count does not allow that retry). Cause found; run them with keep-awake |
| KI-044 | Windows 260-character path limit: a workspace under a very long folder makes an agent's write fail (the run blocks, fail-closed). Existing Part C limit; seen only with a long test folder |
| — | The run graph does not use the sandbox yet (D-47; no agent declares the execute tools). Project runs still use the Part D stand-ins and still report "not verified" |
| — | PostgreSQL is not available in the sandbox (D-16, Part K). D-52 remains dependent on D-21 |
| — | Images must be rebuilt by the owner (`build_sandbox_images.py`) when their dependencies change; this is the only step with network |
| — | Client certificates expire after 2 years (`setup_wsl_docker.py --renew`; the doctor warns 30 days ahead) |

Deviations: DV-023…DV-028 in the tracker (§10), each minimal and explained.

## 10. Checkpoint commit IDs

| Checkpoint | Commit |
|---|---|
| E.1 Engine, mutual TLS, lease, contracts, S-1 | `aab0205` |
| E.2 Runner, Resource Guard, locked images, S-2, S-3 | `5c06d3d` |
| E.3 Recipes, execute tools, execution service, runners | `cc3fb9e` |
| E.4 Starter-kit verification (AC-E-01) | `15e8f61` |
| E.5 Integration, E2E-3, fixes KI-040/KI-041, docs, this report | `fc60c70` |
| Release 0.5.0 (version, CHANGELOG, trackers) | the release commit on `part/e-sandbox` (`git log --grep "release 0.5.0"`) |
| Merge into `main` | the merge commit `merge: Part E sandbox into main (v0.5.0)` |
| Final verification record | the commit on `main` that records the final run; tags `v0.5.0` and `part-e-approved` point to it |

## 11. Branch and push status

- G-E approved and push authorised by the owner (AP-022). Merged into `main` (`--no-ff`), version `0.5.0`,
  annotated tags `v0.5.0` and `part-e-approved`. No force-push; history not rewritten.
- Pushed to `origin` without force (TR-0132): `main`, `part/b-intake`, `part/c-harness-core`, `part/d-orchestration`, `part/e-sandbox` and the tags `v0.4.0`, `part-d-approved`, `v0.5.0`, `part-e-approved`. Every branch and tag ID was compared with GitHub and matches.

## Acceptance criteria

| Criterion | Result |
|---|---|
| AC-E-01 Both templates build and pass their tests with network disabled | **Met:** E.4 run 2 and E2E-3 |
| AC-E-02 All isolation tests pass | **Met:** isolation suite with negative controls, S-1, S-2, S-3 (see §4) |
| AC-E-03 Peak memory recorded under `low_memory` | **Met:** E2E-3, 98.3 MB and 278.3 MB against a 1,536 MB limit |
| AC-E-04 No code path executes generated code on the host | **Met:** static scan, import contracts, no fallback branch, Docker-absent test |

## Parts A–D remain intact

- **Every earlier test still runs.** None was deleted or skipped to get a green result. Four approved test
  files were changed, each recorded: DV-023 (doctor), DV-025 (tool catalogue), DV-027 (a flaky tamper case
  made strict), and the security matrix (new routes).
- **Part D's real-model E2E-2** found KI-040 and passes after the fix (TR-0129).
- **Approved Part C/D code was changed minimally and additively** (DV-025, DV-028):
  - the gateway holds the shared lease;
  - the worker also runs verifications;
  - the runtime wires the sandbox;
  - there is no graph wiring (D-47).
- **The real database** is untouched; the rehearsal on a copy shows that migration 0007 keeps every row and
  the audit chain intact.

**Part E is approved (G-E, AP-022) and released as `0.5.0`. Part F has not been started**; it needs its own plan and approval.
