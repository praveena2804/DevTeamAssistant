import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const STATUS = {
  configured: true,
  reachable: true,
  engine_version: "29.1.3",
  operating_system: "Ubuntu 24.04.4 LTS",
  reason: null,
  checked_at: "2026-11-23T22:00:00+00:00",
  images: [{ name: "python_api", tag: "devteam-sandbox/python_api:1", image_id: "sha256:97050d65714", audit: "clean" }],
  lock_holder: null,
};

const PASSED = {
  verification_id: "tvf_01JD3X5Q0000000000000000AA",
  template_id: "html_fastapi_sqlite",
  template_version: "1.0.0",
  status: "passed",
  recipes: [
    {
      recipe: "pytest_api",
      status: "completed",
      exit_code: 0,
      tests: { tests: 3, passed: 3, failed: 0, errors: 0, skipped: 0 },
      duration_ms: 4200,
      peak_memory_mb: 120.5,
      reason: null,
    },
  ],
  duration_ms: 61000,
  peak_memory_mb: 410.2,
  requested_by: "prn_01JD3X5Q0000000000000000AA",
  requested_at: "2026-11-23T22:00:00Z",
  finished_at: "2026-11-23T22:01:01Z",
  reason: null,
};

function kits(latest: unknown) {
  return [
    {
      template_id: "html_fastapi_sqlite",
      version: "1.0.0",
      description: "Plain HTML frontend with FastAPI.",
      frontend: "html",
      self_test: ["ruff_check", "pytest_api"],
      latest,
    },
  ];
}

afterEach(() => vi.restoreAllMocks());

describe("Sandbox page (Part E)", () => {
  it("shows the engine, the images and an honest verification result", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/sandbox/status") return json(STATUS);
      if (url === "/api/sandbox/templates") return json(kits(PASSED));
      return json({ detail: { message: "unexpected" } }, 404);
    });
    render(
      <MemoryRouter initialEntries={["/sandbox"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Docker Engine 29\.1\.3 on Ubuntu/)).toBeInTheDocument();
    expect(screen.getByText(/vulnerability audit: clean/)).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("3 passed, 0 failed, 0 errors")).toBeInTheDocument();
  });

  it("says Blocked when the engine is not usable", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/sandbox/status")
        return json({ ...STATUS, reachable: false, reason: "sandbox.docker_unavailable: not reachable" });
      if (url === "/api/sandbox/templates") return json(kits(null));
      return json({}, 404);
    });
    render(
      <MemoryRouter initialEntries={["/sandbox"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Not usable: sandbox\.docker_unavailable.*Blocked/)).toBeInTheDocument();
    expect(screen.getByText("Never verified.")).toBeInTheDocument();
  });

  it("queues a verification and disables the button while it is queued", async () => {
    let latest: unknown = null;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/sandbox/status") return json(STATUS);
      if (url === "/api/sandbox/templates") return json(kits(latest));
      if (url === "/api/sandbox/templates/html_fastapi_sqlite/verifications" && init?.method === "POST") {
        latest = { ...PASSED, status: "queued", recipes: [], duration_ms: null, peak_memory_mb: null, finished_at: null };
        return json(latest, 201);
      }
      return json({}, 404);
    });
    render(
      <MemoryRouter initialEntries={["/sandbox"]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Verify" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Verification in progress" })).toBeDisabled());
    expect(
      fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/verifications") && init?.method === "POST"),
    ).toBe(true);
  });
});
