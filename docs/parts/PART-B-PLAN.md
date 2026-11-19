# Part B — Project Intake, Document Extraction, Human IAM & Audit Foundation — Plan

Roadmap ref: §13 Part B · Milestone: M0 · Gate: **G-B** · Working Plan §20 Part B · Date: 2026-11-19

| Field | Value |
|---|---|
| Status | **Draft, awaiting owner confirmation.** No Part B code has been written |
| Preconditions | G-A approved (**pending**); this plan confirmed (**pending**); D-11, D-15, D-22, D-29, D-31 confirmed (see §2) |
| Harness components | HC-15 (human IAM), HC-18 (API rate limits), HC-20 (upload validation), HC-21 (audit store foundation) — steps HS-02…HS-05 |
| Agents | **None.** Roadmap §13 B: "Agents: none" |

---

## 0. Part B in simple words

Part B is the **front door** of DevTeam AI. When it is done you will be able to:

1. **Create a project.** Each project is its own folder ("workspace") and its own records. One project can never see another's files.
2. **Upload your BRD** (the business requirements document) as PDF, Word (DOCX) or plain text. The system checks each file before accepting it:
   - Is it really the type it claims?
   - Is it too large?
   - Is it password-protected or damaged?

   A bad file gets a clear message; the good files still go through.
3. **See what was read from it.** The text is taken out of the document and split into small numbered pieces ("chunks"). Each piece remembers where it came from: file, page, section heading and paragraph. You can click through and check every piece against the original.
4. **Know that only you can use it.** Only someone holding the local key on this computer can use the system. Websites and other programs cannot drive it.
5. **See a record of every action.** Every change (project created, file uploaded, rejected request) is written to a log that cannot be edited ("audit trail").

Part B does **not** analyse the BRD. Nothing reads its meaning yet: there is no AI and no agent. The BRD Analyst arrives in Part H. Part B only prepares clean, referenced text for it.

---

## 1. Scope

**In scope (roadmap §13 B):**
- Data models + migrations: `Project`, `SourceDocument`, `DocumentChunk`, `AuditEvent` (foundation), `Principal` + role assignment.
- Workspace manager: `workspaces/<project_id>/{uploads,extracted,generated,runs,package}`.
- Upload validation (HC-20).
- Extractors (PDF, DOCX, TXT, plus MD subject to §2) and a heading-aware deterministic chunker.
- HC-15 human IAM: loopback binding, local API token, restrictive CORS.
- HC-18 API rate limits on upload and mutation routes.
- HC-21 audit store for user actions.
- API: projects, upload, documents, extraction detail, paginated chunks, audit list.
- UI: Projects dashboard, Create/Upload, Extraction review, sign-in screen.
- Fixture corpus `tests/fixtures/brds/`.

**Out of scope (later parts):**
- Any agent or LLM call (C onward).
- Hash-chained audit and tool/model event types (C, HS-11).
- Agent and service principals, policy engine (C).
- RAG/embedding of chunks (F).
- XLSX, images/OCR, ZIP and other supporting file types (FR-002 "only when validated and supported"; Spec §5.1 "may be added only after validation and resource testing").
- Background extraction worker (D). B extracts synchronously within strict limits.
- Project deletion (see D-31).

---

## 2. Decisions needed and proposed rulings

| ID | Decision | Proposed ruling | Notes |
|---|---|---|---|
| D-11 | Platform UI login | **(a)** No username/password. Loopback + per-install token; role checks stay in code | Roadmap recommendation |
| D-15 | Platform-UI E2E automation | **(b)** API-level E2E script + manual UI demo (Playwright deferred to R) | Saves memory on this machine |
| **D-22** | Supported file types | **Discrepancy, owner to rule.** Spec §5.1/FR-001: "PDF, DOCX and TXT initially". Roadmap D-22: "PDF, DOCX, TXT, **MD**". Options: **(a)** follow the spec (PDF/DOCX/TXT); **(b)** also accept `.md`, read as plain text (headings recognised from `#` lines). (b) adds no new dependency or parser and no new attack surface beyond TXT. Recommendation: **(b)**. The working plan already says "TXT/MD". If the owner prefers the spec wording, MD is removed by deleting one allowlist entry | Recorded as KI/decision, not silently resolved |
| D-29 | Local API authentication | **(a)** See §5.3 for the mechanism | Roadmap recommendation |
| D-31 | Project deletion and retention | Roadmap recommends **(a)** "explicit delete behind an approval", but the Approval Service (HC-13) only arrives in Part C. **Proposal: no delete endpoint in Part B.** Deletion is added after C, once D-31 is ruled | **Deviation from "projects CRUD"** (roadmap §13 B). Owner to accept. Until then, delete a test project's folder by hand |

**Plan-level proposals (owner may change any value; all live in config):**

| Setting | Proposed default | Reason |
|---|---|---|
| Max file size | 20 MB per file | Typical BRDs are under 5 MB. Bounds memory during extraction |
| Max files per upload | 10 | Bounds a single request |
| Max PDF pages | 300 | Bounds CPU time (synchronous extraction) |
| Max DOCX uncompressed size / entries | 100 MB / 2,000 entries; compression ratio ≤ 100:1 | Zip-bomb protection (DOCX is a ZIP) |
| Max extracted characters per document | 2,000,000 | Bounds DB and memory; longer documents are truncated with a warning |
| Chunk target size | ≈1,200 characters; never splits mid-paragraph unless one paragraph is longer than 2,000 characters | Fits easily in a 4,096-token context later (Spec §9.3) |
| Rate limits | Upload: 10 requests/min; other mutations: 60/min; reads: unlimited on loopback | HC-18; prevents runaway scripts |
| Where chunk text lives | Chunk text + references in SQLite; full extraction JSON in `workspaces/<id>/extracted/` | Chunks are small and need fast paging; the large raw output stays in the workspace (Spec §12) |

---

## 3. Contracts / interfaces touched

No `schema_version` contracts exist yet (they start in Part C). Part B adds:
- Alembic revision `0002_intake_iam_audit`.
- OpenAPI additions (snapshot `apps/api/openapi.json` updated; web client regenerated).
- Pydantic API models in `devteam_api.schemas`.

Part C will lift shared records into `devteam_core.contracts` without changing the API.

---

## 4. Data model (migration `0002`)

All IDs use the Part A prefixed-ULID scheme (`devteam_core.ids`) except chunk IDs, which must be **deterministic**.

| Table | Key columns | Notes |
|---|---|---|
| `principals` | `id` (`prn_…`), `kind` (`human`), `display_name`, `created_at` | One local human principal, created on first start |
| `principal_roles` | `principal_id` FK, `role` | Roles from Spec §4: `project_user`, `project_admin`, `reviewer`. The single local user holds all three (D-11) |
| `projects` | `id` (`prj_…`), `name` (1–100 chars, unique), `description`, `status` (`active`), `created_by` FK, `created_at`, `updated_at` | |
| `source_documents` | `id` (`doc_…`), `project_id` FK, `role` (`primary_brd` / `supporting`), `original_filename` (display only), `stored_name`, `media_type`, `size_bytes`, `sha256`, `status` (`accepted` / `extracted` / `failed` / `rejected`), `error_code`, `error_message`, `page_count`, `warnings` JSON, `extractor` + `extractor_version`, `created_at` | Unique (`project_id`, `sha256`) → duplicate upload detected, not stored twice |
| `document_chunks` | `id` (`chk_…`), `project_id` FK, `document_id` FK, `ordinal`, `kind` (`text` / `table`), `text`, `section_path` (e.g. `2 Scope > 2.1 Users`), `page_start`, `page_end`, `paragraph_start`, `paragraph_end`, `reference` (human-readable), `content_sha256` | Index (`document_id`, `ordinal`) |
| `audit_events` | `id` (`aud_…`), `ts`, `principal_id`, `action` (e.g. `project.create`, `document.upload`, `auth.denied`), `project_id` (nullable), `target_type`, `target_id`, `outcome` (`success` / `denied` / `error`), `request_id`, `trace_id`, `details` JSON (**redacted**) | **Append-only**: SQLite triggers reject `UPDATE` and `DELETE`. Hash chain added in Part C |

Every domain table carries `project_id` (Plan §11). Repositories require it in every query.

**Deterministic chunk ID:** `chk_` + first 26 hex characters of `sha256(document.sha256 ‖ chunker_version ‖ ordinal)`. The same file and the same chunker always give the same IDs (tested).

**Human-readable reference:** e.g. `BRD.pdf · p.4 · §2.1 Users · ¶3–5`.

---

## 5. Design

### 5.1 Packages and dependency rules

| Package | New / existing | Contents in Part B |
|---|---|---|
| `packages/harness` → `devteam_harness` | **New** | `iam/` (principals, roles, token, cookie session, auth dependency), `audit/` (append-only store + writer), `ratelimit/` (in-memory token bucket), `validation/uploads.py` (HC-20 rules) |
| `packages/services` → `devteam_services` | **New** | `extractor/` (`pdf.py`, `docx.py`, `text.py`, `chunker.py`, `models.py`), `workspace/` (workspace manager) |
| `devteam_state` | Existing | Models + repositories for the tables above; migration `0002` |
| `devteam_api` | Existing | Routers `auth`, `projects`, `documents`, `audit`; middleware `auth`, `cors`, `ratelimit`; error envelope |

Import-linter contracts are extended per roadmap §7.1:
- `harness` must not import `services`, `apps` or `agents`;
- `services` must not import `apps` or `agents`;
- `state` must not import `harness`, `services` or `apps`.

### 5.2 Upload validation (HC-20, HS-02)

Checks run in this order. The first failure rejects that file only.

1. **Filename:**
   - Unicode-normalise (NFC) and strip directory parts.
   - Reject: empty names, names > 120 characters, control characters, `..`, absolute paths, drive letters, UNC paths, `:` (NTFS alternate data streams).
   - Reject Windows reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`, with or without extension) and trailing dots or spaces.
   - The original name is kept **only for display**. Files are stored as `<document_id><ext>`, so a user-supplied name never becomes a path.
2. **Extension allowlist:** `.pdf`, `.docx`, `.txt` (+ `.md` if D-22 (b)).
3. **Size:** > 0 bytes and ≤ limit. The limit is enforced while streaming, so an oversized upload is never fully buffered.
4. **Magic bytes / structure** (pure Python, no `libmagic`):
   - **PDF:** starts with `%PDF-`.
   - **DOCX:** a valid ZIP containing `[Content_Types].xml` and `word/document.xml`, within the zip-bomb limits.
   - **TXT/MD:** no NUL bytes; decodes as text.
   - Extension and content must agree, so an `.exe` renamed `.pdf` is rejected.
5. **Checksum:** SHA-256 computed while streaming.
6. **Encrypted/corrupt detection:** `pypdf` reports encrypted PDFs; extraction errors become per-file errors.

Every rejection has a stable `error_code` (e.g. `upload.unsupported_type`, `upload.type_mismatch`, `upload.too_large`, `upload.encrypted`, `upload.corrupt`, `upload.empty`, `upload.unsafe_filename`) and a plain message.

### 5.3 Local authentication (HC-15, D-29 (a), HS-03)

- **Token:**
  - On first start, the API generates a random 256-bit token (`secrets.token_urlsafe(32)`) and stores it in `data/api_token` (gitignored, created owner-only where Windows ACLs allow).
  - The token is registered with the Part A redaction registry, so it can never appear in logs.
  - (Part C moves it to the keyring via `SecretsManager`, per D-18.)
- **How the browser signs in** (like Jupyter):
  1. `scripts/dev.ps1` prints and opens a one-time sign-in link: `http://127.0.0.1:5173/#/signin?token=…`. The token travels in the URL **fragment**, which browsers never send to servers or proxies.
  2. The sign-in page posts it once to `POST /api/session`.
  3. The API answers with an `HttpOnly`, `SameSite=Strict`, `Path=/api` session cookie. Page scripts cannot read it.
  4. The page then removes the token from the address bar.
  - A manual "paste token" box is offered as a fallback.
- **Scripts and tests** may send `Authorization: Bearer <token>` instead.
- **Every `/api/*` route requires authentication**, except `POST /api/session`. `/health` and `/version` stay public (they reveal nothing sensitive).
- **Token comparison** uses `hmac.compare_digest`.
- **Cross-site protection:**
  - CORS allows only the UI origin (`http://127.0.0.1:5173` and `http://localhost:5173`).
  - Mutating requests must carry an allowed `Origin`, or none at all (non-browser clients using the bearer token).
  - Together with `SameSite=Strict`, this blocks a malicious website from using your session.
- **Binding:** the API still binds `127.0.0.1` only (Part A). A non-loopback `api.host` is rejected at config load, the same pattern as the Ollama URL.
- **Principal and roles:** the authenticated request resolves to the single local principal with all three roles. Each route still declares the role it needs (`require_role("project_user")`), so multi-user can be added later without rewriting routes (Spec §4).

### 5.4 Rate limiting (HC-18, HS-04)

- An in-memory token bucket keyed by principal and route group: no new dependency.
- An exceeded limit returns `429` with `Retry-After` and writes an audit event.
- Limits are set in `config/defaults.yaml` (`ratelimit:` section). Part F adds `config/rate_limits.yaml` for gateway limits.

### 5.5 Audit (HC-21 foundation, HS-05)

- A FastAPI dependency wraps **every mutating route**. It writes exactly one `AuditEvent` per request, including denied and failed ones (`auth.denied`, `ratelimit.exceeded`, validation errors).
- Audit details pass through the Part A `redact_value`.
- Rows cannot be changed or removed (database triggers; tested).
- `GET /api/projects/{id}/audit` and `GET /api/audit` (paginated) let the owner see the trail.

### 5.6 Extraction and chunking

| Format | Library | What is kept |
|---|---|---|
| PDF | `pypdf` (encryption check, page count) + `pdfplumber` (text per page, simple tables) | Page numbers. Headings inferred from font size/boldness where available, otherwise none. Tables become `table` chunks |
| DOCX | `python-docx` | Heading styles (`Heading 1..n`) → section path; paragraphs; tables (as `table` chunks). DOCX has no fixed pages, so page is `null` and section + paragraph are the reference (FR-003 "where possible") |
| TXT / MD | `charset-normalizer` (UTF-8, UTF-8-BOM, cp1252, …) | Paragraphs split on blank lines; MD `#` headings → section path; detected encoding recorded as a warning when not UTF-8 |

- **Chunker:**
  - Walks the structured output in document order.
  - Starts a new chunk at every heading.
  - Packs whole paragraphs up to the target size.
  - The chunker version is recorded on each document. Same input gives the same chunks and the same IDs.
- **Warnings** (shown in the UI): empty pages (possible scanned image; OCR is out of scope), truncated text, non-UTF-8 encoding, no headings found.
- **Safety:**
  - Extraction runs in the API process **only on validated files**, within page, size and time limits.
  - `python-docx` parses XML with entity resolution disabled.
  - Extraction is synchronous in Part B; the worker in D will take it over.

### 5.7 API (all under `/api`, all authenticated except `POST /api/session`)

| Method | Path | Purpose | Audited |
|---|---|---|---|
| POST / GET / DELETE | `/api/session` | Sign in (token → cookie) / who am I / sign out | Yes (sign-in success and failure) |
| GET / POST | `/api/projects` | List / create | POST |
| GET / PATCH | `/api/projects/{project_id}` | Detail / rename or edit description | PATCH |
| POST | `/api/projects/{project_id}/documents` | Multipart upload of 1–10 files; **per-file result list** (accepted, extracted or rejected, with reason) | Yes, one event per file |
| GET | `/api/projects/{project_id}/documents` | List documents and status | — |
| GET | `/api/projects/{project_id}/documents/{document_id}` | Extraction detail: metadata, warnings, sections outline | — |
| GET | `/api/projects/{project_id}/documents/{document_id}/chunks?offset&limit` | Paginated chunks with references | — |
| GET | `/api/projects/{project_id}/audit`, `/api/audit` | Audit trail (paginated) | — |

**Cross-project isolation:** every document and chunk lookup filters by **both** `project_id` and `document_id`. A valid document ID under the wrong project returns `404` (not `403`), so it reveals nothing.

### 5.8 UI (`apps/web`)

| Screen | What you can do |
|---|---|
| Sign-in | Arrive via the link from `dev.ps1`, or paste the token |
| Projects dashboard | See projects, document counts, last activity; create a project |
| Project page / upload | Drag-and-drop or choose files; per-file result with plain-language errors; document list with status |
| Extraction review | Left: section outline. Right: chunks with their reference (file · page · section · paragraph) and warnings; paging |
| Activity (audit) | Table of recorded actions with outcome |

There are no new web dependencies: React Router already exists, and the typed client is regenerated from the OpenAPI snapshot.

---

## 6. Files to create / modify

**Create:**
- `packages/harness/pyproject.toml`; `packages/harness/src/devteam_harness/{__init__.py, py.typed, iam/{__init__,token,principals,dependencies}.py, audit/{__init__,store,writer}.py, ratelimit/{__init__,bucket}.py, validation/{__init__,uploads}.py}`; `packages/harness/tests/…`
- `packages/services/pyproject.toml`; `packages/services/src/devteam_services/{__init__.py, py.typed, extractor/{__init__,models,pdf,docx,text,chunker,service}.py, workspace/{__init__,manager}.py}`; `packages/services/tests/…`
- `packages/state/src/devteam_state/models/{principal,project,document,audit}.py`, `repositories/{__init__,projects,documents,audit,principals}.py`, `migrations/versions/0002_intake_iam_audit.py`
- `apps/api/src/devteam_api/routers/{auth,projects,documents,audit}.py`, `middleware/{auth,ratelimit}.py`, `schemas.py`, `deps.py`, `errors.py`
- `apps/web/src/pages/{SignInPage,ProjectsPage,ProjectPage,ExtractionReviewPage,AuditPage}.tsx`, `src/features/upload/…`, tests for each page
- `tests/fixtures/brds/` + `tests/fixtures/make_fixtures.py`:
  - generated deterministically: a sample BRD as DOCX (headings + table) and as a multi-page PDF; TXT in UTF-8 and cp1252; MD;
  - bad files: encrypted PDF, corrupt DOCX, disguised `.exe`→`.pdf`, empty file, zip-bomb DOCX.
  - The oversized case is generated at test time, not committed.
- `tests/integration/test_intake_flow.py`, `tests/security/{test_auth,test_upload_attacks,test_isolation,test_audit_append_only,test_ratelimit,test_route_inventory}.py`, `tests/e2e/test_e2e_1_intake.py`
- `docs/parts/PART-B-COMPLETION-REPORT.md` (at the end)

**Modify:**
- Root `pyproject.toml` (workspace members, import-linter contracts, isort first-party)
- `config/defaults.yaml` + `settings.py` (`uploads`, `auth`, `ratelimit` sections; API host loopback validator)
- `apps/api/src/devteam_api/main.py` (routers, middleware, token bootstrap)
- `apps/api/openapi.json`, `apps/web/src/api/schema.d.ts` (regenerated)
- `apps/web/src/{App.tsx, components/Layout.tsx, api/client.ts}`
- `scripts/dev.ps1` (sign-in link), `scripts/doctor.py` (token file present)
- `README.md`, `CLAUDE.md` (if a convention is added), `CHANGELOG.md`
- Trackers: `docs/IMPLEMENTATION_TRACKER.md`, `docs/PROJECT_PROGRESS_TRACKER.md`

---

## 7. Dependencies to add (runtime; all free and permissively licensed)

| Package | Constraint | Licence | Reason |
|---|---|---|---|
| `pypdf` | `>=5,<7` | BSD-3 | Encryption detection, page count; also builds encrypted-PDF test fixtures |
| `pdfplumber` | `>=0.11,<0.12` | MIT (pulls `pdfminer.six` MIT, `pypdfium2` Apache-2.0/BSD, `Pillow` MIT-CMU) | Per-page text and tables (roadmap §13 B) |
| `python-docx` | `>=1.1,<2` | MIT (pulls `lxml` BSD) | DOCX headings, paragraphs and tables |
| `charset-normalizer` | `>=3.3,<4` | MIT | TXT/MD encoding detection |
| `python-multipart` | `>=0.0.9` | Apache-2.0 | FastAPI file uploads |

**Not added:**
- PyMuPDF (AGPL, roadmap §9.1);
- `python-magic` (needs a native library on Windows);
- `slowapi` (a token bucket is ~40 lines);
- Playwright (D-15 (b)).

Exact versions are fixed by `uv lock`. `pip-audit` runs after installation.

---

## 8. Tests mapped to acceptance criteria

| AC | Tests |
|---|---|
| **AC-B-01** Upload DOCX/PDF/TXT, see content with references | Unit: each extractor on fixtures (page numbers, heading paths, tables, encodings); chunk-ID determinism (same input twice → same IDs; chunker-version change → different IDs); reference formatting. Integration: upload → workspace file → DB rows → `GET …/chunks` returns references. Web: Vitest for upload results and extraction review rendering |
| **AC-B-02** Unreadable/protected files: clear per-file error; others continue | Rejection matrix: encrypted PDF, corrupt DOCX, disguised extension, empty, oversized (streamed), zip bomb, unsafe filenames (traversal, absolute, drive, UNC, ADS `:`, reserved names, trailing dot, control characters, 300-character name), duplicate upload. Mixed batch: 2 good + 3 bad → 2 extracted, 3 rejected with codes, all audited |
| **AC-B-03** Cross-project access impossible via API or paths | Two projects: every document/chunk/audit route with the other project's IDs → `404`; property test: stored paths always resolve inside `workspaces/<project_id>/`; hostile filenames never influence the storage path |
| **AC-B-04** Every mutating call authenticated and audited | **Route inventory test:** enumerates every route in the OpenAPI schema; every non-GET route under `/api` must reject a missing token, a wrong token and a disallowed `Origin`, and must write an audit row. A new route added later without auth fails this test automatically. Plus: cookie flags (`HttpOnly`, `SameSite=Strict`); token never in logs (canary); audit triggers block `UPDATE`/`DELETE`; `429` after the limit, audited |
| **AC-B-05** Spec scenario #1 (extraction part) | `tests/e2e/test_e2e_1_intake.py` (E2E-1): start the real API (uvicorn subprocess, as in Part A), sign in with the bearer token, create a project, upload the complete sample BRD (DOCX + PDF), assert every requirement heading in the fixture is present with the correct page/section reference |

**Also:**
- Regression of every Part A suite.
- ruff, mypy strict, import-linter (with the new contracts), agent count, gitleaks, pip-audit, npm audit.
- Web lint, typecheck, test, build.
- OpenAPI drift check.

Safety-critical modules (auth, upload validation, audit) are written **test-first** (Working Plan §2.5).

---

## 9. Demonstration script (G-B)

1. `./scripts/dev.ps1`: the browser opens the sign-in link; the dashboard appears.
2. Create the project "Library Demo".
3. Upload `sample_brd.docx`, `sample_brd.pdf` and `corrupt.docx` together. The two BRDs show as extracted; `corrupt.docx` shows "This file is damaged or not a real Word document".
4. Open the extraction review. Click a chunk; its reference (page/section/paragraph) matches the original.
5. Open the Activity page: create, three upload events (one rejected).
6. In a terminal, run `curl -X POST http://127.0.0.1:8000/api/projects` without the token → `401`, and a new `auth.denied` audit row appears.
7. Try to read project A's document through project B's URL → `404`.

---

## 10. Risks specific to Part B

| Risk | Mitigation |
|---|---|
| Large or complex PDFs are slow or memory-heavy on this machine | Page cap, per-page processing, size limit; extraction time recorded in the completion report |
| Headings in PDFs are unreliable (PDFs have no real heading structure) | References always include the page number; heading inference is best-effort and warned; DOCX gives reliable headings |
| Scanned (image-only) PDFs yield no text | Detected (empty pages) and reported as a warning; OCR is out of scope (Spec §5.1) |
| Token handling mistakes expose the API | Fragment-based link, HttpOnly cookie, Origin check, route inventory test, redaction canary |
| `pdfplumber` brings native wheels (`pypdfium2`) | Wheels exist for Windows/Linux and CPython 3.12. Verified at install time; if one is unavailable, fall back to `pypdf` text extraction and record a deviation |
| Spec/roadmap mismatch on MD (D-22) | Owner ruling before implementation |
| No deletion (D-31 proposal) | Documented; manual cleanup of test projects until deletion is added after C |

---

## Owner confirmation

- ☐ G-A approved on —
- ☐ This plan confirmed on — (including rulings on D-11, D-15, D-22, D-29, D-31 and the limits in §2)
