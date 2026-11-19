import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const version = {
  name: "DevTeam AI",
  version: "0.1.0",
  api_version: "v1",
  python_version: "3.12.6",
  profile: "test",
  schema_revision: "0001_baseline",
  schema_head: "0001_baseline",
};

function mockApi(health: unknown, healthStatus = 200) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/health") return jsonResponse(health, healthStatus);
    if (url === "/version") return jsonResponse(version);
    return jsonResponse({ detail: "not found" }, 404);
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App shell", () => {
  it("renders healthy status from a mocked /health", async () => {
    const fetchMock = mockApi({ status: "ok", checks: { state_db: { status: "ok", detail: null } } });
    renderAt("/");
    expect(await screen.findByTestId("overall-status")).toHaveTextContent("Healthy");
    expect(screen.getByRole("cell", { name: "state_db" })).toBeInTheDocument();
    expect(screen.getByText("0001_baseline")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/health", expect.anything());
  });

  it("shows degraded status when /health answers 503", async () => {
    mockApi({ status: "degraded", checks: { state_db: { status: "error", detail: "schema behind" } } }, 503);
    renderAt("/");
    expect(await screen.findByTestId("overall-status")).toHaveTextContent("Degraded");
    expect(screen.getByText("schema behind")).toBeInTheDocument();
  });

  it("reports an unreachable API", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("unreachable");
  });

  it("raises ApiError text for unexpected HTTP errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "boom" }, 500));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
  });

  it("routes unknown paths to the not-found page", () => {
    mockApi({ status: "ok", checks: {} });
    renderAt("/does-not-exist");
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
