# ADR-0002: Exactly ten agents, including the Head Agent

- **Status:** Accepted. Mandated by the owner (roadmap §9.3; Spec §1.1, §6).
- **Date:** 2026-11-19
- **Related:** roadmap §4.4

## Decision

The platform has exactly ten logical agents:

1. BRD Analyst
2. Requirements Finalizer
3. Head Agent
4. Architecture Agent
5. Database Developer
6. Backend Developer
7. Frontend Developer
8. Testing & Validation Agent
9. Debugger & Code Reviewer
10. Documentation & Reporting Agent

Services, harness components, Head Agent internals and graph structural nodes are never agents.

**Five-layer enforcement:**

| Layer | Mechanism |
|---|---|
| L1 | closed `AgentRole` enum with 10 members |
| L2 | `AgentRegistry` refuses to start unless exactly 10 specs are registered |
| L3 | `scripts/check_agent_count.py` checks the `agents/` packages and the workspace members |
| L4 | a test on the compiled graph checks the agent nodes |
| L5 | governance: `CLAUDE.md` |

## Consequences

- Adding an eleventh agent is prohibited.
- Any change to `AgentRole`, `agents/` or the registry requires an ADR and owner approval.
- L3 exists as a skeleton from Part A and becomes active in Part C.
