# ADR-0006: API and Worker process split; SQLite domain truth vs checkpoint control state

- **Status:** Proposed. Recommended by roadmap §9.3; implemented in Part D. Owner approval pending.
- **Date:** 2026-11-19
- **Related:** roadmap §3.1, §6.7; decision D-29

## Decision

**Two processes:**

- `apps/api` serves HTTP and SSE and writes user-driven records.
- `apps/worker` runs one LangGraph run at a time. It uses a job table, a single-run lock and a heartbeat, and recovers stale runs on start.

**Two databases:**

- `data/state.db` (Alembic-managed) holds domain truth.
- `data/checkpoints.db` holds LangGraph control state only.
- `RunState` holds references, never facts.

## Consequences

- A long CPU-bound run cannot block the API.
- A crashed worker resumes from its checkpoint, and the ledger prevents duplicated side effects.
- SQLite contention is handled with WAL, a busy timeout and short transactions; WAL and the busy timeout are configured in Part A.
