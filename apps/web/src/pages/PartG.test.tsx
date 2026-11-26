import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function entry(key: string, extra: Record<string, unknown>) {
  return {
    key, display_name: key, category: "frontend", declared_status: "supported", effective_status: "available",
    reason: null, templates: [], substitutions: [], known_limitations: [], available_from_part: null,
    verification_ids: [], ...extra,
  };
}

const CATALOG = {
  catalog_hash: "d".repeat(64),
  demoted: 1,
  entries: [
    entry("react_vite", { display_name: "React (Vite, TypeScript)", verification_ids: ["tvf_1"] }),
    entry("postgresql_docker_local", {
      display_name: "PostgreSQL (local Docker, testing)", category: "database",
      declared_status: "supported_for_local_testing", effective_status: "demoted",
      reason: "not available yet: arrives in Part K", substitutions: ["sqlite"], available_from_part: "K",
    }),
    entry("angular", {
      display_name: "Angular", declared_status: "unsupported", effective_status: "unsupported",
      reason: "no Angular starter kit", substitutions: ["react_vite", "html_css_js"],
    }),
  ],
};

const SETTINGS: Record<string, unknown> = {
  "/api/settings/models": {
    models: [], slots: [], agent_overrides: 0, operation_overrides: 0, keep_alive: "0s",
    response_cache: false, ollama_version: null,
  },
  "/api/settings/profile": {
    profile: "low_memory", max_model_ram_mb: 3072, min_free_ram_mb: 3072, ram_margin_mb: 512,
    sandbox_memory_mb: 1536, sandbox_cpus: 1.5,
  },
  "/api/settings/limits": { rate_limits: {}, rag: {}, context: {}, llm: {} },
  "/api/settings/evals": [],
};

afterEach(() => vi.restoreAllMocks());

describe("Capability Catalog panel (Part G)", () => {
  it("shows declared and effective status, demotion reasons and alternatives, read-only", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/settings/catalog") return json(CATALOG);
      if (url in SETTINGS) return json(SETTINGS[url]);
      return json({ detail: { message: "not part of this test" } }, 404);
    });
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );
    const heading = await screen.findByRole("heading", { name: "Capability Catalog" }, { timeout: 5000 });
    expect(heading).toBeInTheDocument();
    expect(screen.getByTestId("catalog-line").textContent).toContain("3 entries; 1 demoted");
    const postgres = screen.getByTestId("catalog-postgresql_docker_local");
    expect(postgres.textContent).toContain("DEMOTED");
    expect(postgres.textContent).toContain("arrives in Part K");
    expect(screen.getByTestId("catalog-angular").textContent).toContain("react_vite, html_css_js");
    expect(screen.getByTestId("catalog-react_vite").textContent).toContain("available");
    expect(calls.every((c) => c.startsWith("GET "))).toBe(true);
  });
});

function item(name: string, extra: Record<string, unknown>) {
  return {
    item_id: `fea_${name}`, subject_kind: "technology_need", subject_name: name, layer: "frontend",
    version_spec: null, catalog_key: null, status: "supported", reason: null, alternative: null,
    substitution: "none", requirement_ids: ["FR-001", "FR-002"], whole_application: true, gap: null,
    source: "doc_1 p. 1", ...extra,
  };
}

function report(extra: Record<string, unknown>) {
  return {
    report_id: "fsr_1", run_id: "run_1", created_at: "2026-11-26T10:00:00Z", catalog_hash: "c".repeat(64),
    verification_ids: [], numerator: 0, denominator: 2, coverage_pct: 0, threshold_pct: 50,
    recommendation: "stop_recommended", proceed_allowed: true, needs_acknowledgement: true, needs_identified: true,
    blocked_keys: [], pending_keys: ["FR-001", "FR-002"], partial_keys: [], demoted: [],
    items: [
      item("Angular", {
        catalog_key: "angular", status: "supported_with_substitution", reason: "Angular is not supported",
        alternative: "react_vite", substitution: "offered",
      }),
    ],
    effects: [], ...extra,
  };
}

const WAITING = {
  id: "run_1", project_id: "prj_1", status: "waiting_for_human", pending_gate: "feasibility_decision",
};

function mockFeasibility(body: unknown, posts: { url: string; body: unknown }[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      return json({ run_id: "run_1", gate: "feasibility_decision", approval_id: "apr_1", decision: "approved" });
    }
    if (url.endsWith("/feasibility")) return json(body);
    if (url.endsWith("/runs/run_1")) return json(WAITING);
    return json({ detail: { message: "not part of this test" } }, 404);
  });
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/projects/prj_1/runs/run_1/feasibility"]}>
      <App />
    </MemoryRouter>,
  );
}

describe("Feasibility page (Part G)", () => {
  it("shows the formula and needs the low-coverage confirmation before proceeding", async () => {
    const posts: { url: string; body: unknown }[] = [];
    mockFeasibility(report({}), posts);
    renderPage();
    expect(await screen.findByRole("heading", { name: "Feasibility" }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByTestId("coverage-line").textContent).toContain("0 / 2 = 0.0% (threshold 50%)");
    expect(screen.getByTestId("recommendation").textContent).toContain("Stop recommended");
    expect(screen.getByTestId("item-Angular").textContent).toContain("react_vite (offered — not applied)");
    const proceed = await screen.findByRole("button", { name: "Proceed" });
    expect(proceed).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(proceed).toBeEnabled();
    fireEvent.click(proceed);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toEqual({ decision: "approved", acknowledge_low_coverage: true });
  });

  it("disables proceeding when nothing can be built and cancels with a revise reason", async () => {
    const posts: { url: string; body: unknown }[] = [];
    mockFeasibility(
      report({
        recommendation: "nothing_to_build", proceed_allowed: false, needs_acknowledgement: false,
        blocked_keys: ["FR-001", "FR-002"], pending_keys: [],
        items: [item("Power Apps", { catalog_key: "power_apps", status: "not_supported", reason: "not supported" })],
      }),
      posts,
    );
    renderPage();
    expect(await screen.findByTestId("proceed-disabled", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Proceed" })).toBeNull();
    expect(screen.getByTestId("blocked").textContent).toBe("FR-001, FR-002");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "revise" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toEqual({ decision: "rejected", reason: "revise" });
  });

  it("says plainly when no technology needs were identified", async () => {
    mockFeasibility(
      report({
        numerator: 2, coverage_pct: 100, recommendation: "proceed", needs_acknowledgement: false,
        needs_identified: false, pending_keys: [], items: [],
      }),
      [],
    );
    renderPage();
    expect(await screen.findByTestId("no-needs", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Proceed" })).toBeEnabled();
  });
});
