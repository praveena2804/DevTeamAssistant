# Part B — Project Intake, Document Extraction, Human IAM & Audit Foundation — Completion Report

Roadmap ref: §13 Part B · Milestone: M0 · Gate: **G-B** · Date: 2026-11-19 · Authorisation: AP-005 · Branch: `part/b-intake` (local)

## Summary

Part B delivers the platform's front door:

- create projects;
- upload BRDs (PDF, DOCX, TXT, MD);
- validate every file;
- extract the text into referenced pieces;
- review the extracted text in the UI;
- keep projects isolated from each other;
- authenticate every API call with the local key;
- rate-limit changes;
- record every change and every refused request in an append-only activity log.

**Agents:** none, as roadmap §13 B requires. **Model calls:** none.

**Harness components (foundations only):**

| Component | What Part B built |
|---|---|
| HC-15 | Local human principal and roles, install token, session cookie |
| HC-18 | API rate limits |
| HC-20 | Upload validation |
| HC-21 | Append-only audit store; the hash chain comes in Part C |

None of these components is complete yet; each grows in later parts (roadmap §12.1).

## Acceptance criteria

| AC | Result | Evidence |
|---|---|---|
| AC-B-01 User uploads DOCX/PDF/TXT and sees extracted content with source references | **Pass** (API + UI component tests). Also MD (D-22 (b)) | `test_extractors.py` (every format finds every heading; PDF pages exact), `test_chunker.py` (references, determinism, property test), `test_intake_api.py`, `PartB.test.tsx` (extraction review shows references), E2E-1 |
| AC-B-02 Unreadable or password-protected files: clear per-file error; others continue | **Pass** | 10-file mixed batch (`test_mixed_batch…`): 2 good + 8 bad (corrupt, encrypted PDF, password-protected DOCX, disguised .exe, traversal name, scanned/no text, empty, zip bomb). Each bad file gets a code and a plain message; nothing of it is kept |
| AC-B-03 Cross-project access impossible via API or paths | **Pass** | `test_projects_cannot_reach_each_others_documents` (404 across projects), malformed/unknown IDs → 404, hostile filenames never touch the filesystem (stored names are always `doc_…`), workspace path checks |
| AC-B-04 Every mutating API call authenticated and audited | **Pass** | Route inventory (from routers, cross-checked with live OpenAPI): every `/api` route has `authenticate`; every mutation has `audited`. Missing/wrong bearer, wrong cookie and raw token used as cookie are all 401 and audited. A bad Origin is 403 and audited; rate-limit 429 is audited; 422/409 are audited as errors; the database rejects UPDATE/DELETE on `audit_events` |
| AC-B-05 Spec scenario #1 (extraction part) | **Pass** | `tests/e2e/test_e2e_1_intake.py` against the real uvicorn server: sign in → project → upload DOCX + PDF + corrupt DOCX → every heading present; FR text found under "3 Functional Requirements" with the correct page (PDF p.2) / section reference; activity rows present |

## Tests executed (actual, 2026-11-19)

| Suite | Command | Passed | Failed | Skipped |
|---|---|---|---|---|
| Python fast suite (Parts A+B: unit, integration, security, contract, scripts, E2E-1) | `uv run pytest -m "not llm and not docker and not slow and not acceptance" --cov` | **279** | 0 | 0 |
| Web | `npm run check:api / lint / typecheck / test / build` (from PowerShell, KI-012) | **13** (Vitest) + 4 clean gates | 0 | 0 |
| Static | `ruff check`, `ruff format --check`, `mypy packages apps scripts` (strict), `lint-imports` (4 contracts), agent count | all clean | — | — |
| Smoke | `./scripts/dev.ps1 -Smoke` (real API + Vite: health, `/api` 401 without key, 200 with key) | SMOKE OK | — | — |
| Security scans | Gitleaks (exit 0), pip-audit (no known vulnerabilities), npm audit (0) | clean | — | — |
| Performance | 300-page generated PDF, extraction + chunking | 38.8 s, +12 MB peak memory, 600 chunks | — | — |

**Failures during development, all fixed and recorded in the tracker (TR-0018…TR-0033):**

| Kind | Count | What happened |
|---|---|---|
| Product bug | 1 | python-docx raised an unhandled error type on a truncated .docx. It is now reported as "damaged file". The file check normally stops such files earlier |
| Test bugs | 6 | Wrong redaction helper; route listing broken by a FastAPI internals change; two over-strict expectations; a server-output pipe that filled up and blocked the server; an exact-text match |
| Environment | 1 | Vitest fails when started from a lower-case `c:` path (KI-012) |

**Not run:** no `docker`, `llm`, `slow` or `acceptance` tests exist for Part B.

**Manual UI demo:** not performed by the coding agent (no browser). The UI is covered by component tests and the API by E2E-1, per D-15 (b). The owner's demo is the G-B demonstration below.

## Coverage

94% total branch coverage, Parts A and B. The new modules range from 83% (`pdf.py`; untested lines are rare malformed-PDF branches) to 100%. Two files sit at 0% because they only run as subprocesses:

- `__main__.py`: covered functionally by E2E-1 and the smoke test;
- `openapi_export.py`: 67%, unchanged from Part A.

## Security controls in this part

| Area | Controls |
|---|---|
| **Authentication** (D-29 (a)) | Per-install 256-bit key in `data/api_token` (gitignored), registered for log redaction. The browser gets an HttpOnly, SameSite=Strict cookie scoped to `/api` that is *derived* from the key; the raw key is never a valid cookie. Constant-time comparison |
| **Cross-site protection** | Unsafe requests with a cookie must carry an allowed loopback Origin. CORS only for the UI origin. API host and allowed origins must be loopback (validated at start-up) |
| **Upload validation** (HC-20) | Safe-filename rules: traversal, absolute, drive, UNC, NTFS streams, reserved names, control characters, trailing dots/spaces. Plus: extension allowlist; streamed size cap (requests over the total cap are refused before reading, 413/411); content signature (disguised executables rejected); DOCX zip-bomb limits; encrypted/corrupt detection; duplicate detection. Stored names come from document IDs only |
| **Isolation** | Every query is filtered by project. A valid document ID under another project returns 404. Workspace paths are verified to stay inside the project folder |
| **Rate limits** (HC-18) | Uploads 10/min, other changes 60/min, sign-in attempts 10/min; refusals are audited |
| **Audit** (HC-21) | One event per change (same transaction on success, separate transaction on failure). One event per uploaded file. Refused and invalid requests are recorded. Details are redacted. Database triggers make the table append-only |
| **Secret hygiene** | Canary tests: the key and session value never reach the database; secrets pasted into a project name are redacted in the audit copy |

## Files created / modified

Exact register: tracker CH-0031…CH-0040.

**New:**

| Location | What |
|---|---|
| `packages/harness` | iam, audit, ratelimit, validation |
| `packages/services` | extractor, workspace, intake |
| `packages/state` | models, repositories, migration `0002` |
| `apps/api` | deps, schemas, body-limit middleware, routers (session/projects/documents/audit), sign-in link |
| `apps/web` | client, `useLoad`, 5 screens, `ErrorNotice`, tests |
| `tests/` | fixtures, security, e2e, integration |

**Modified:** settings + defaults, `main.py`, request-context middleware, `dev.ps1`, `doctor.py`, `pyproject.toml` + `uv.lock`, OpenAPI snapshot + web schema, README, CHANGELOG, CLAUDE.md, trackers, and three Part A tests whose expectations legitimately changed (schema head, public paths).

## Dependencies added

| Package | Version | Licence |
|---|---|---|
| pypdf | 6.21.4 | BSD |
| pdfplumber | 0.11.10 | MIT; pulls pdfminer.six (MIT), pypdfium2 (Apache/BSD), Pillow (MIT-CMU) |
| python-docx | 1.2.0 | MIT; pulls lxml (BSD) |
| python-multipart | 0.0.34 | Apache-2.0 |

charset-normalizer was already present and is now declared. There are no new web dependencies.

## Deviations from plan

| ID | Deviation |
|---|---|
| DV-006 | No project delete endpoint (owner ruling, AP-005) |

**Minor, within the plan's intent:**

- The sample corpus is generated by `tests/fixtures/brds.py` at test time instead of committed binary files.
- API errors are produced by `deps.api_error` instead of a separate `errors.py`.
- The request-level upload audit event is named `document.upload_batch`, to keep it distinct from the per-file `document.upload` events.

## Known issues & limitations

| ID | Issue |
|---|---|
| KI-012 | Vitest must run from PowerShell or an upper-case `C:` path |
| KI-013 | Extraction runs during the upload request: about 40 s for 300 pages. Background extraction arrives with the worker (Part D) |
| — | PDF heading detection is best effort (font size/bold). Page references are always exact |
| — | Scanned PDFs are refused with "no readable text"; OCR is out of scope |
| — | The key is stored in a file until Part C moves it to the OS keyring (D-18) |

## Decisions

| Status | Decisions |
|---|---|
| Applied | D-11 (a), D-15 (b), D-22 (b), D-29 (a), D-31 (deletion excluded from B), and the approved upload limits (AP-005) |
| Still open (asked when needed) | D-31 final ruling; model selection; D-07; D-21; D-26/D-33 confirmation; D-34 |

## Demonstration (G-B)

1. `./scripts/dev.ps1`: the browser opens the sign-in link and lands on Projects.
2. `uv run python tests/fixtures/brds.py data/sample_brds` writes sample files, good and broken.
3. Create "Library Demo". Upload `sample_brd.docx`, `sample_brd.pdf`, `corrupt.docx` and `locked.pdf` together. Two are read; two are refused with plain reasons.
4. Open `sample_brd.pdf` and check the outline and the references (page, section, paragraph).
5. Open Activity: create, the upload events (including the refused ones) and the batch.
6. Run `curl -X POST http://127.0.0.1:8000/api/projects`. It returns 401, and a new "auth.denied / Refused" row appears.

## Approval requested: G-B

Approve gate G-B, or request changes.
