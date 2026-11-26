import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const MODELS = {
  models: [
    {
      tag: "qwen2.5:3b", kind: "chat", approved: true, benchmark_ref: "b.md", peak_ram_mb: 2144, num_ctx: 4096,
      timeout_s: 180, installed: true, digest: "aaaaaaaaaaaa", checked_at: "2026-11-25T10:00:00Z",
    },
    {
      tag: "nomic-embed-text", kind: "embedding", approved: true, benchmark_ref: "e.md", peak_ram_mb: 480,
      num_ctx: 2048, timeout_s: 60, installed: null, digest: null, checked_at: null,
    },
  ],
  slots: [
    { slot: "instruct", model: "qwen2.5:3b", fallbacks: ["qwen2.5:1.5b"] },
    { slot: "embedding", model: "nomic-embed-text", fallbacks: [] },
  ],
  agent_overrides: 0,
  operation_overrides: 0,
  keep_alive: "0s",
  response_cache: false,
  ollama_version: "0.40.18",
};
const PROFILE = {
  profile: "low_memory", max_model_ram_mb: 3072, min_free_ram_mb: 3072, ram_margin_mb: 512,
  sandbox_memory_mb: 1536, sandbox_cpus: 1.5,
};
const LIMITS = { rate_limits: { max_wait_s: 30 }, rag: { top_k: 6 }, context: {}, llm: {} };
const EVALS = [
  { prompt_id: "brd_analyst.system", active_version: 1, eval_required: false, report: "matches" },
  { prompt_id: "head.system", active_version: 1, eval_required: false, report: "none" },
];

afterEach(() => vi.restoreAllMocks());

describe("Settings page (Part F)", () => {
  it("shows models, installation state, slots, profile and evaluation status read-only", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/settings/models") return json(MODELS);
      if (url === "/api/settings/profile") return json(PROFILE);
      if (url === "/api/settings/limits") return json(LIMITS);
      if (url === "/api/settings/evals") return json(EVALS);
      return json({ detail: { message: "unexpected" } }, 404);
    });
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByText("not checked yet (start the worker)")).toBeInTheDocument();
    expect(screen.getByTestId("models-line").textContent).toContain("Ollama 0.40.18");
    expect(screen.getByTestId("profile-line").textContent).toContain("models up to 3072 MB");
    expect(screen.getByText(/instruct: qwen2.5:3b \(if it does not fit: qwen2.5:1.5b\)/)).toBeInTheDocument();
    expect(screen.getAllByText("not yet (stub until its agent part)")).toHaveLength(2);
    expect(calls.every((c) => c.startsWith("GET "))).toBe(true); // read-only: nothing is changed from the UI
  });
});

describe("Search index panel (Part F)", () => {
  it("shows the indexing state and queues a rebuild", async () => {
    const posts: string[] = [];
    let job: Record<string, string | null> = {
      job_id: "rij_1",
      kind: "index",
      status: "blocked",
      created_at: "2026-11-25T10:00:00Z",
      finished_at: "2026-11-25T10:00:01Z",
      error_code: "rag.store_unavailable",
      detail: null,
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/knowledge/index") && init?.method === "POST") {
        posts.push(url);
        job = { ...job, kind: "rebuild", status: "queued", error_code: null };
        return json(job, 202);
      }
      if (url.endsWith("/knowledge/index")) {
        return json({
          project_id: "prj_1", collection: null, embedding_model: null, embedding_digest: null, chunker_version: null,
          documents_extracted: 1, documents_indexed: 0, index_complete: false, latest_job: job,
        });
      }
      if (url.startsWith("/api/projects/prj_1/documents")) return json([]);
      if (url.startsWith("/api/projects/prj_1/runs")) return json([]);
      if (url === "/api/projects/prj_1") {
        return json({ id: "prj_1", name: "Library", description: "", status: "active", created_at: "2026-11-25T10:00:00Z",
                      updated_at: "2026-11-25T10:00:00Z" });
      }
      return json({ detail: { message: "unexpected" } }, 404);
    });
    render(
      <MemoryRouter initialEntries={["/projects/prj_1"]}>
        <App />
      </MemoryRouter>,
    );
    const line = await screen.findByTestId("index-line");
    expect(line.textContent).toContain("0 of 1 document(s) indexed");
    expect(line.textContent).toContain("rag.store_unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Rebuild index" }));
    await waitFor(() => expect(posts).toEqual(["/api/projects/prj_1/knowledge/index"]));
    await waitFor(() => expect(screen.getByTestId("index-line").textContent).toContain("rebuild queued"));
  });
});
