import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

const P = "prj_01JD3X5Q0000000000000000AA";
const R = "run_01JD3X5Q0000000000000000AA";
const BASE = `/api/projects/${P}/runs/${R}`;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    id: R,
    project_id: P,
    run_kind: "initial",
    status: "waiting_for_human",
    stage: "requirements",
    mode: "stub",
    current_node: "project_approval",
    pending_gate: "project_approval",
    control_request: "none",
    spec_version_id: null,
    created_at: "2026-11-23T09:00:00Z",
    updated_at: "2026-11-23T09:05:00Z",
    verified: false,
    label: "Part D trial run: real local AI, stand-in services, nothing built, tested or verified",
    completed_tasks: 2,
    total_tasks: 2,
    last_event_seq: 3,
    blocker: null,
    ...overrides,
  };
}

const EVENTS = [
  { seq: 1, kind: "node.started", ts: "2026-11-23T09:00:01Z", payload: { node: "brd_analyst" } },
  {
    seq: 2,
    kind: "service.recorded",
    ts: "2026-11-23T09:00:02Z",
    payload: { node: "feasibility", status: "not_evaluated", available_in_part: "G" },
  },
  { seq: 3, kind: "gate.opened", ts: "2026-11-23T09:00:03Z", payload: { gate: "project_approval" } },
];

function mockApi(state: { run: Record<string, unknown>; decided?: string[] }) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === BASE && method === "GET") return json(state.run);
    if (url.startsWith(`${BASE}/events?`)) return json(EVENTS);
    if (url === `${BASE}/tasks`)
      return json([
        {
          task_id: "tsk_01JD3X5Q0000000000000000AA",
          agent: "brd_analyst",
          operation_id: "brd_analyst.analyse_section",
          status: "completed",
          attempts: 1,
          max_attempts: 2,
          depends_on: [],
          requirement_ids: [],
          blocker_reason: null,
          files: [],
          agent_returned_output: true,
          harness_verified: false,
        },
      ]);
    if (url === `${BASE}/gate`)
      return json({
        run_id: R,
        gate: "project_approval",
        approval_type: "spec_approval",
        roles: ["project_admin"],
        spec_version_id: "spv_01JD3X5Q0000000000000000AA",
        spec_checksum: "a".repeat(64),
        requirements: [{ key: "FR-001", description: "The librarian can add books." }],
        notes: ["Approving freezes exactly this specification version; building can start only after it."],
      });
    if (url === `${BASE}/gates/project_approval/decision` && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { decision: string };
      state.decided?.push(body.decision);
      return json({ run_id: R, gate: "project_approval", approval_id: "apr_01JD3X5Q0000000000000000AA", decision: body.decision });
    }
    if (url === `${BASE}/trace`)
      return json({
        run_id: R,
        trace_id: "a".repeat(32),
        spans: [
          { span_id: "1", parent_span_id: null, name: "graph.run", start_ts: "2026-11-23T09:00:00Z", duration_ms: 900, status: "unset", attributes: { outcome: "waiting" } },
          { span_id: "2", parent_span_id: "1", name: "graph.node.brd_analyst", start_ts: "2026-11-23T09:00:01Z", duration_ms: 800, status: "unset", attributes: { outcome: "ok" } },
        ],
      });
    return json({ detail: { code: "test.unmocked", message: `unmocked ${method} ${url}` } }, 404);
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Build progress (Part D)", () => {
  it("labels the run honestly and never shows a verified result", async () => {
    mockApi({ run: run() });
    renderAt(`/projects/${P}/runs/${R}`);
    expect(await screen.findByRole("note")).toHaveTextContent("nothing built, tested or verified");
    expect(await screen.findByText("no (nothing is verified in Part D)")).toBeInTheDocument();
    expect(await screen.findByText(/feasibility: not evaluated \(stand-in, arrives in Part G\)/)).toBeInTheDocument();
  });

  it("shows the requirements being approved and records the decision", async () => {
    const state = { run: run(), decided: [] as string[] };
    mockApi(state);
    renderAt(`/projects/${P}/runs/${R}`);
    expect(await screen.findByText(/The librarian can add books\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(state.decided).toEqual(["approved"]));
  });

  it("explains why a run stopped", async () => {
    mockApi({ run: run({ status: "blocked", pending_gate: null, blocker: "the build did not complete" }) });
    renderAt(`/projects/${P}/runs/${R}`);
    expect(await screen.findByText("the build did not complete")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
  });
});

describe("Trace viewer (Part D)", () => {
  it("shows each step nested under the run", async () => {
    mockApi({ run: run() });
    renderAt(`/projects/${P}/runs/${R}/trace`);
    expect(await screen.findByText("graph.node.brd_analyst")).toBeInTheDocument();
    expect(screen.getByText("graph.run")).toBeInTheDocument();
  });
});
