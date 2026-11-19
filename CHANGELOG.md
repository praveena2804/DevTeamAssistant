# Changelog

One section per approved part (roadmap §8). Entries are added at gate approval; work awaiting approval is listed under *Unreleased*.

**Versioning:** SemVer, pre-1.0. Each approved part bumps the minor version (Part A = 0.1.0, B = 0.2.0, …, R = 0.18.0); fixes to an approved part bump the patch version. The accepted MVP is `1.0.0-mvp`. Git tags: `vX.Y.Z` plus `part-<letter>-approved` (roadmap §16.2).

## 0.1.0 — Part A: Foundation, Repository Conventions & Environment Benchmark (G-A approved 2026-11-19)

### Added

- uv workspace with three packages:
  - `devteam_core`: layered validated configuration; structured JSON logging with correlation fields and secret redaction; W3C trace context; IDs; clock; paths.
  - `devteam_state`: SQLite engine with WAL and foreign keys; Alembic baseline; online backup.
  - `devteam_api`: `/health`, `/version`, request-ID/trace middleware, OpenAPI export.
- React + TypeScript + Vite UI shell with a system status page and a typed client generated from OpenAPI.
- Scripts: `doctor`, `benchmark_models`, `check_agent_count` (skeleton), `backup_state`, `export_openapi`, `setup.ps1`, `dev.ps1`.
- Repository conventions: `CLAUDE.md`, `README.md`, `.gitignore`, `.gitattributes`, `.dockerignore`, `.env.example`, `.pre-commit-config.yaml`.
- CI workflow definitions: `ci.yml` and `security.yml`. These are not yet run on GitHub; no push has been authorised.
- ADR-0001…ADR-0007, the Part A plan, and the baseline model benchmark.
