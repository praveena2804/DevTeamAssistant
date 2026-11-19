# ADR-0001: LangGraph is the orchestration framework

- **Status:** Accepted. Mandated by the owner (roadmap §9.3).
- **Date:** 2026-11-19
- **Related:** Spec §8; roadmap §6; HC-07

## Context

The platform needs:

- a durable workflow with legal stage ordering;
- human-approval interrupts;
- conditional routing, bounded retry loops, and checkpoint/resume after crashes.

All of this must run on a CPU-only laptop.

## Decision

Use LangGraph (with `langgraph-checkpoint-sqlite`) as the only orchestration framework.

**Graph shape:**

- a parent graph `project_run`;
- subgraphs `discovery`, `build`, `delivery` and `change_request`.

**State and routing:**

- Thread ID = `run_id`.
- Checkpoints are stored in `data/checkpoints.db`, separate from the domain database `data/state.db`.
- Routing is done by pure functions returning a closed `NodeName` enum, validated against a static allowed-transition table.

## Consequences

- No other orchestrator may be introduced.
- LangGraph re-executes an interrupted node from its start, so the following are mandatory:
  - side-effect-free gate nodes;
  - an attempt ledger;
  - two-phase external actions (roadmap §6.7).
- Versions are pinned; the graph is wrapped by `devteam_orchestration` so that API churn stays localised.
