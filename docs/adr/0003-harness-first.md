# ADR-0003: Harness-first architecture; gateway-only models; tool-layer-only side effects

- **Status:** Accepted. Mandated by the owner (roadmap §9.3).
- **Date:** 2026-11-19
- **Related:** Spec §11; roadmap §5; HC-01…HC-21; decisions D-35 and D-36

## Decision

**One shared harness.** `packages/harness` provides the single harness for all ten agents. The Head Agent has no exception.

**Fixed execution path.** Every agent invocation follows the same lifecycle (roadmap §5.2):

1. identity;
2. effective-permission computation (AgentSpec ∩ central policy ∩ task scope ∩ stage);
3. scoped context;
4. input validation and guardrails;
5. the agent itself, through `AgentContext` proxies only;
6. output validation and guardrails;
7. persistence through repositories;
8. audit and trace.

**Fail closed.** If a mandatory control is missing, the task is BLOCKED.

**Models.** All model calls go through `LLMGateway`. It talks to Ollama's HTTP API directly, on loopback only.

**No LiteLLM (D-35).** Owner direction 2026-11-19: do not add LiteLLM without a documented impact assessment and owner approval. The reason is that our gateway must remain the only routing, retry, fallback, caching and policy layer.

**Side effects.** All file, command and external effects go through the Controlled Tool Execution Layer (HC-06). There is no generic shell tool.

**Skills (D-36).** Skills are versioned instruction bundles inside the prompt/skill registry (HC-16). They grant no permissions.

**Security is enforced in code.** Prompts are not security controls.

## Consequences

The harness interfaces exist before any agent (Part C), and they are proven first with a dummy agent and then with ten stubs (Part D). Real models come in Part F. The bypass vectors in roadmap §5.3 are tested in CI.
