# Feasibility report

Written by the Feasibility Gate (deterministic service; no model was used) from the stored records.

| Field | Value |
|---|---|
| Project | prj_01KFE0000000000000000000PG |
| Run | run_01KFE0000000000000000000PG |
| Report | fsr_01KFE0000000000000000000G1 |
| Date (UTC) | 2026-11-26T00:00:00+00:00 |
| Capability Catalog | sha256 cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc |
| Template verifications relied on | tvf_01KFE0000000000000000000V1, tvf_01KFE0000000000000000000V2 |

## Coverage

```text
coverage = buildable in-scope FR and BR keys / in-scope FR and BR keys x 100
         = 0 / 5 = 0.0%   (threshold 50%)
```

Buildable means supported, or every substitution it relies on is approved. Partial items are listed separately and never counted as buildable. Non-functional requirements are reported, not counted.

## Recommendation

**Stop recommended**: coverage is below the threshold. You may still proceed with the buildable scope, but only after explicitly acknowledging this.

## Items by status

| Subject | Source | Catalog key | Status | Reason | Alternative | Affected requirements |
|---|---|---|---|---|---|---|
| Azure AD | doc_01KFE0000000000000000000PG p. 4 | `enterprise_identity_provider` | not supported | Enterprise identity provider is not supported: needs an external identity provider (D-04) | — | FR-004 |
| Stripe | doc_01KFE0000000000000000000PG p. 6 | `payment_gateway` | not supported | Payment gateway is not supported: needs credentials and handles money; not supported | — | FR-003 |
| a relational database | doc_01KFE0000000000000000000PG p. 4 | not in the catalog | supported with substitution | &#39;a relational database&#39; names no database engine; SQLite (the platform default) is offered | `sqlite` (offered) | all in-scope requirements (5) |
| React 18 | doc_01KFE0000000000000000000PG p. 3 | `react_vite` | partial | version 18 requested; the starter kit provides 19 | — | all in-scope requirements (5) |
| FastAPI | doc_01KFE0000000000000000000PG p. 4 | `fastapi_python` | supported | — | — | all in-scope requirements (5) |

## Blocked requirements

- **FR-003**: blocked by fea_01KFE000000000000000000005
- **FR-004**: blocked by fea_01KFE000000000000000000004

## Partial items

- React 18: version 18 requested; the starter kit provides 19

## Substitutions

- a relational database → `sqlite`: offered — not applied

## Gaps raised for the clarification form

- technology / unclear: Database engine not specified; if accepted, SQLite is used and disclosed in the summary (Spec §13.2).

## Catalog entries demoted at evaluation time

- `postgresql_docker_local`: not available yet: arrives in Part K
- `github_public_repo`: not available yet: arrives in Part P
- `github_actions_ci`: not available yet: arrives in Part P
- `github_pages`: not available yet: arrives in Part P

## What "supported" means today

"Supported" means the platform has a verified starter kit and verification recipes for the capability (Capability Catalog, effective status). Generating application code for it arrives with the developer agents (Parts K-L). Nothing in this report was built, run or tested.
