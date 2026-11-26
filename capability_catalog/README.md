# Capability Catalog

What the DevTeam AI platform can build (Spec §13). The Feasibility Gate (Part G) checks every
technology, integration, login method and deployment target in a BRD against this catalog.

| File | Purpose |
|---|---|
| `capabilities.yaml` | The entries: one per `CatalogKey` (closed enum in `devteam_core.contracts.catalog`) |
| `schemas/capabilities.schema.json` | JSON Schema of the file, generated from the contract (`scripts/export_contract_schemas.py`) |

## Declared and effective status

The `status` written in `capabilities.yaml` is a **declaration**. It is never trusted on its own. The
platform computes an **effective status** every time it starts and every time it checks a BRD:

| Effective status | When |
|---|---|
| `available` | Declared supported, one of its starter kits is installed with a valid manifest, every recipe it names is registered, and the kit's newest finished verification **passed** and is **current** (same kit checksum and image IDs) |
| `available_limited` | As `available`, but with a declared limitation (local testing only, static frontend only, public network without credentials) |
| `demoted` | Declared supported, but one of the conditions above fails, or its assets arrive in a later part (for example PostgreSQL in Part K, GitHub delivery in Part P). The reason is shown |
| `unsupported` | Declared unsupported (licensed, paid, credentialed or simply not built) |
| `later_change_request` | Can be added later through a change request (Part Q) |

"Supported" today means: a verified starter kit and its recipes exist. Generating application code for
it arrives with the developer agents (Parts K–L).

## Mapping BRD wording

Mapping is deterministic. The names the BRD Analyst extracts are normalised (lower case, single spaces)
and compared with each entry's `aliases` within the category that matches the technology layer.
A key proposed by a model is accepted only if it is a `CatalogKey` member of the right category;
anything else is rejected and audited (`catalog.key_rejected`).

Wording that names no database engine ("SQL", "a relational database") is listed under
`ambiguous_database_aliases`. It is never mapped to an enterprise product: SQLite is offered, pending
the user's acceptance, and a clarification gap is raised (Spec §13.2).

## Changing the catalog

A catalog change is a reviewed code change: see `docs/runbooks/capability-catalog.md`.
