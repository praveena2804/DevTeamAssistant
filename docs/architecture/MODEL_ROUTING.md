# Model Routing — Audit and Proposed Design

| Field | Value |
|---|---|
| Status | **Implemented in Part F (2026-11-25)** as described in §3–§4: `config/models.yaml`, `devteam_harness.llm_gateway.router` (precedence, start-up rules, recovery ladder), `ModelCall` records. D-06 and D-37 ruled (AP-023). The §1 audit table below is the historical 2026-11-19 snapshot; see `docs/architecture/llm-gateway.md` for the current behaviour |
| Date | 2026-11-19 |
| Requested by | Owner instruction "Model Routing Audit, Part B Planning, and Simple-Words Progress Tracker" (2026-11-19) |
| Sources checked | Spec §9.1–§9.3, §10; roadmap §5.1 (HC-02…HC-05, HC-18), §9.1, §13 F, §20 (D-06, D-28); Working Plan §8 (GW-01…GW-12); `config/`, `packages/core/src/devteam_core/config/settings.py`, `scripts/benchmark_models.py`; `docs/benchmarks/2026-11-19-baseline.md`; `ollama list` |
| Decisions involved | D-06 (per-role overrides, recommended (b)); **D-37 (new, proposed): per-operation overrides and a review slot**; D-28 (response cache); model selection approval (Plan §20 A [APPR]) |

---

## 1. Audit: what exists today

| Capability | Status | Evidence |
|---|---|---|
| Ollama address restricted to this machine (loopback) | **Implemented (Part A)**, tested | `OllamaSettings._loopback_only` in `devteam_core/config/settings.py`; `test_config.py` |
| One global request timeout for Ollama (`ollama.request_timeout_s`) | **Implemented (Part A)** as a setting only. Nothing uses it yet | `config/defaults.yaml` |
| One global context size (`resources.llm_context_tokens`) | **Implemented (Part A)** as a setting only. Nothing uses it yet | `config/defaults.yaml`, profiles |
| Benchmark grouped by slot (instruct / coder / fallback) | **Implemented (Part A)**. This is a *measurement tool*, not routing | `scripts/benchmark_models.py` `SLOTS` |
| Doctor checks Ollama reachability and env vars | **Implemented (Part A)** | `scripts/doctor.py` |
| LLM Gateway (HC-02), the single path to any model | **Not implemented.** Interface + fake provider in **C**; real gateway in **F** | Roadmap §12.1 |
| Model abstraction and routing table, `config/models.yaml` (HC-03) | **Not implemented.** File does not exist. Planned for **F** (GW-04) | Roadmap §7, §13 F |
| Different models for instruct and coding work | **Planned (F)**: slots in Spec §9.2 | Spec §9.2; GW-04 |
| Different model per agent (per-role override, D-06) | **Planned (F)**, D-06 still *Open* (recommended (b), "configuration only, evidence-based") | Roadmap §20 |
| Different model per *operation* inside an agent | **Not planned anywhere.** Gap; proposed here as D-37 | — |
| Separate review model for code review | **Not planned.** Spec §9.2 puts Debugger/Reviewer on the coder slot. Proposed here as an optional slot (D-37) | Spec §9.2 |
| Optional larger coder (7B) for hard backend/debug tasks | **In the spec, not yet in the roadmap routing.** Must stay disabled until benchmarked (Spec §9.2, §9.3) | Spec §9.2 |
| Embedding model configuration | **Not implemented.** Planned for **F** (HC-08). **No embedding model is downloaded** on this machine and none was benchmarked (KI-011) | `ollama list`; roadmap §9.1 |
| Smaller fallback model and fallback ladder (HC-04) | **Not implemented.** Planned for **F** (GW-06). Candidate `qwen2.5:1.5b` measured in Part A | Benchmark TR-0016 |
| Model-specific timeouts and resource settings | **Not implemented.** Only global values exist today | — |
| Usage tracking per call: tokens, time, RAM (HC-05) | **Not implemented.** Budgets in **C**; tokens/RAM in **F** | Roadmap §12.1 |
| Safe model switching and unloading (`keep_alive: 0`, check `/api/ps`) | **Not implemented.** Planned for **F** (GW-11) | Plan §8 |
| Pause on low memory instead of crashing (`WAITING_FOR_RESOURCES`) | **Not implemented.** Planned for **F** (GW-06) and **E** (Resource Guard) | Spec §9.1 |
| Only one LLM call at a time | **Not implemented.** Process lock in **C** (GW-02); cross-process lock in **F** (GW-07) | Plan §8 |
| Agents cannot call Ollama directly | **Not implemented** (no agents exist yet). Import-linter + banned-API rules activate in **C** | Roadmap §4.4, §5.3 |
| Add or replace a model without changing agent code | **Design intent** of HC-03 (role → slot → model from config). Not implemented | Roadmap §5.1 |

**Summary:** Part A built *no* model routing. It built the safety rule that the model server must be on this machine, two global settings, and a benchmark that measures candidate models. Every routing capability is planned for Part C (interfaces) and Part F (real implementation).

### Models currently on this machine

| Model | Benchmarked (TR-0016)? | Use |
|---|---|---|
| `qwen2.5:3b` | Yes, 3/3 | Instruct candidate |
| `qwen2.5-coder:3b` | Yes, 3/3 | Coder candidate |
| `qwen2.5:1.5b` | Yes, 3/3 | Fallback candidate |
| `llama3.2:3b` | Yes, 3/3 | Not recommended (slower, ~400 MB more RAM) |
| `qwen2.5:7B`, `qwen3:8B`, `qwen3:latest` | **No** | Must not be used until measured. Free RAM was too low to try them safely |
| `qwen2.5:0.5b`, `llama3.2:1b` | Pipeline smoke only / no | Not candidates |
| Embedding model (e.g. `nomic-embed-text`) | **Not installed** | To be chosen by benchmark in F |

**None of these models is approved.** The benchmark recommendation is waiting for the owner.

---

## 2. Gap against the owner's routing intent

| Owner requirement | Covered by current plan? | What is needed |
|---|---|---|
| Different models for coding and instruction tasks | Yes (slots, F) | — |
| Different models for different agents | Yes, if D-06 (b) is ruled | Owner ruling on D-06 |
| Per-agent overrides | Yes, if D-06 (b) | Same |
| Per-operation model selection | **No** | New decision **D-37** + operation declarations in `AgentSpec` (Part C) |
| Separate embedding model configuration | Yes (F) | Benchmark an embedding model in F |
| Smaller fallback | Yes (F) | Approve candidate |
| Configurable review model | **No** | Part of D-37: optional `review` slot that defaults to `coder` |
| Model-specific timeout and resource settings | Partially (global only) | Per-model block in `models.yaml` (F) |
| Replace models without touching agent code | Yes (design intent) | Enforce "no model names in agent code" with a lint rule (C) |

**Where each change lands, if approved:**
- **Part C:** `AgentSpec` declares the agent's *operations* and each operation's default slot. `ModelRequest` carries the operation ID. The executor checks the operation belongs to the calling agent.
- **Part F:** `config/models.yaml`, the router, the validator, fallback, unloading.
- No change to Parts A or B, and no change to the ten-agent structure.

---

## 3. Proposed routing design

### 3.1 Vocabulary

| Term | Meaning | Example |
|---|---|---|
| **Model** | One concrete Ollama model tag, with its measured limits | `qwen2.5-coder:3b` |
| **Slot** | A *kind* of model the platform needs | `instruct`, `coder`, `review`, `coder_large`, `fallback`, `embedding` |
| **Agent** | One of the ten canonical agents | `backend_developer` |
| **Operation** | A named kind of model call that an agent makes, declared in its `AgentSpec` | `backend_developer.generate_code` |

Operation IDs are always prefixed by the agent name, so an operation override can never apply to the wrong agent.

### 3.2 Precedence (most specific wins)

| Order | Rule | Example |
|---|---|---|
| 1 | **Per-operation override** | `debugger_reviewer.review_code → review` slot |
| 2 | **Per-agent override** | `architect → model qwen2.5:3b` |
| 3 | **Slot default.** The slot comes from the operation's declaration in `AgentSpec`, otherwise from the agent's default slot (Spec §9.2) | `coder → qwen2.5-coder:3b` |
| 4 | **Global default** (only used if a slot has no model) | `instruct` |

An override may name **either** a slot **or** a model, never both. The router records which rule matched (`route_rule: operation|agent|slot|global`) on the `ModelCall` record, so every choice can be explained afterwards.

**Fallback is not part of precedence.** It is applied *after* resolution and only when the chosen model fails for resource reasons (out of memory, too slow, load failure). It always steps **down** the ladder: never to a larger model, never to an external provider.

**Embeddings are separate.** The `embedding` slot cannot be overridden per agent or operation. The same embedding model must be used for indexing and querying (Spec §10). Changing it triggers an index rebuild.

### 3.3 Conflict and validation rules (checked when the platform starts; any failure stops start-up)

1. Every model named anywhere must be declared in `models:`.
2. Every agent key must be one of the ten canonical agents.
3. Every operation key must be declared by that agent's `AgentSpec` (checked from Part C onward).
4. Every slot referenced must exist. `review` and `coder_large` may be `null`. A `null` slot falls back to the slot named in its `inherit` field.
5. An override has exactly one of `slot` or `model`.
6. Duplicate keys in the YAML are a load error, not "last one wins".
7. A model can be routed to only if `approved: true` **and** it has a `benchmark_ref`. This is D-06's "evidence-based" rule. An unbenchmarked model (for example `qwen2.5:7B` today) cannot be selected by configuration.
8. A model's measured `peak_ram_mb` must fit the active hardware profile's `max_model_ram_mb`. Otherwise the route is rejected at start-up, not at run time.
9. Fallback ladders contain only models whose `peak_ram_mb` is less than or equal to the model they back up.
10. `base_url` stays loopback-only (already enforced in Part A).
11. Agents never see or choose model names. A lint rule in Part C rejects model tags inside `agents/**`. Agents request an **operation**; the gateway resolves the model.
12. An agent cannot borrow another agent's operation to get a different model. The executor rejects an operation not declared in the caller's `AgentSpec`, and audits it.

### 3.4 Draft `config/models.yaml` (illustrative)

This is a draft for the Part F plan. It is **not** created in the repository yet: nothing would read it, and an unused config file could be mistaken for a working feature. Model tags are the **unapproved** benchmark candidates.

```yaml
schema_version: 1
default_slot: instruct                 # precedence rule 4

models:                                # catalogue; numbers from docs/benchmarks/2026-11-19-baseline.md
  qwen2.5:3b:
    approved: false                    # owner approval pending
    benchmark_ref: docs/benchmarks/2026-11-19-baseline.md
    peak_ram_mb: 2144
    num_ctx: 4096
    timeout_s: {connect: 5, read: 300, total: 600}
    keep_alive: 0s                     # unload right after each call (Spec §9.3)
  qwen2.5-coder:3b:
    approved: false
    benchmark_ref: docs/benchmarks/2026-11-19-baseline.md
    peak_ram_mb: 2153
    num_ctx: 4096
    timeout_s: {connect: 5, read: 420, total: 900}
    keep_alive: 0s
  qwen2.5:1.5b:
    approved: false
    benchmark_ref: docs/benchmarks/2026-11-19-baseline.md
    peak_ram_mb: 1208
    num_ctx: 4096
    timeout_s: {connect: 5, read: 240, total: 480}
    keep_alive: 0s

slots:
  instruct:    {model: qwen2.5:3b}
  coder:       {model: qwen2.5-coder:3b}
  review:      {model: null, inherit: coder}           # D-37: configurable review model
  coder_large: {model: null, inherit: coder}           # Spec §9.2: 7B only after a benchmark
  fallback:    {model: qwen2.5:1.5b}
  embedding:   {model: null}                           # chosen by benchmark in Part F (KI-011)

fallback_ladders:                       # used only on OOM / timeout / load failure
  instruct: [qwen2.5:1.5b]
  coder:    [qwen2.5:1.5b]
  review:   [qwen2.5:1.5b]

agents:                                 # precedence rule 2 (D-06); default slots from Spec §9.2
  brd_analyst:            {slot: instruct}
  requirements_finalizer: {slot: instruct}
  head:                   {slot: instruct}
  architect:              {slot: instruct}
  documenter:             {slot: instruct}
  database_developer:     {slot: coder}
  backend_developer:      {slot: coder}
  frontend_developer:     {slot: coder}
  tester:                 {slot: coder}
  debugger_reviewer:      {slot: coder}

operations:                             # precedence rule 1 (D-37); empty by default
  debugger_reviewer.review_code: {slot: review}
```

### 3.5 Owner's routing intent, mapped

| Operation (owner's list) | Agent | Proposed operation ID | Slot | Source of the default |
|---|---|---|---|---|
| BRD analysis | BRD Analyst | `brd_analyst.analyse_section` | instruct | Spec §9.2 |
| Requirements analysis | Requirements Finalizer | `requirements_finalizer.*` | instruct | Spec §9.2 |
| Planning / ambiguous failure classification | Head Agent | `head.plan`, `head.classify_failure` | instruct | Spec §9.2 |
| Architecture reasoning | Architecture Agent | `architect.design` | instruct | Spec §9.2 |
| Code generation | Database / Backend / Frontend Developer | `*.generate_code` | coder | Spec §9.2 |
| Test generation | Testing & Validation Agent | `tester.generate_tests` | coder | Spec §9.2 |
| Debugging and code repair | Debugger & Code Reviewer | `debugger_reviewer.repair` | coder (`coder_large` optional, once benchmarked) | Spec §9.2 |
| Code review | Debugger & Code Reviewer | `debugger_reviewer.review_code` | **review** (inherits coder until configured) | D-37 proposal |
| Documentation writing | Documentation & Reporting Agent | `documenter.write_section` | instruct | Spec §9.2 |
| Embeddings | — (RAG service, HC-08) | `rag.embed` | embedding | Spec §9.2, §10 |

The exact operation names are fixed in the Part C contracts. The names above are proposals.

---

## 4. Hardware rules on this 16 GB, CPU-only machine

| Rule | How the design enforces it | Part |
|---|---|---|
| Only one LLM call at a time | Gateway lock: in-process (C), cross-process (F) | C, F |
| Only one model loaded | Every call uses `keep_alive: 0s`. Before loading a different model, the gateway asks Ollama to unload the previous one and checks `/api/ps`. Owner env vars `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1` are a second layer (KI-004) | F |
| Pause instead of crashing on low memory | Before each call: free RAM ≥ model `peak_ram_mb` + profile margin. If not → try the fallback ladder → if still not, checkpoint and `WAITING_FOR_RESOURCES` | F (+E Resource Guard) |
| Never run an LLM and a Docker sandbox together | Shared Resource Guard lock | E, F |
| Measured numbers only | Rule 7 (`approved` + `benchmark_ref`); rule 8 (`peak_ram_mb` fits the profile) | F |
| Switching cost is visible | Measured load times: 2–7 s per model (TR-0016). Router logs every switch; usage (HC-05) records load time separately from generation time | F |

**Known cost:** routing instruct work and code work to different models means switching models during a run. With 3B models this costs seconds per switch, not minutes (TR-0016). That is acceptable. It is one reason not to add many distinct models.

---

## 5. Decisions this needs

| ID | Question | Recommendation | Needed by |
|---|---|---|---|
| Model selection | Approve the benchmark candidates for instruct, coder and fallback | Approve `qwen2.5:3b` / `qwen2.5-coder:3b` / `qwen2.5:1.5b` as **initial** values; revisit at F and R | Before F (not needed for B) |
| D-06 | Per-agent overrides | **(b)**, as in the roadmap: configuration only, and only for benchmarked models | F plan |
| **D-37 (new, proposed)** | Per-operation overrides and an optional `review` slot (defaults to coder), with the precedence in §3.2 | **Adopt.** Additive; spec defaults unchanged; small cost (operation IDs in `AgentSpec`, Part C) | **C plan**, because `AgentSpec` is frozen there |
| Embedding model | Which model | Benchmark 1–2 small candidates in F (for example `nomic-embed-text`); downloading one is a Part F action | F |
| Larger coder (7B) | Allow `coder_large` | Keep disabled until benchmarked with enough free RAM. Free RAM today makes this unlikely | F / R |

Adopting D-37 changes roadmap §5.1 (HC-03 row), §13 C/F and §20, and Working Plan GW-04. These are **proposed amendments**, not applied, to be made in one roadmap revision with D-35/D-36 (DV-003) once approved.
