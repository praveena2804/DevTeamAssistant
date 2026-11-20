import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi(integrity: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/audit/verify") return json(integrity);
    if (url.startsWith("/api/audit?")) return json({ items: [], total: 0, offset: 0, limit: 50 });
    return json({ detail: { code: "test.unmocked", message: `unmocked ${url}` } }, 404);
  });
}

function renderActivity() {
  return render(
    <MemoryRouter initialEntries={["/activity"]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Activity log integrity (HC-21)", () => {
  it("shows that the log is intact", async () => {
    mockApi({ ok: true, checked: 41, pre_chain_rows: 9, broken_at: null, reason: null });
    renderActivity();
    expect(await screen.findByText(/Log intact: 50 entries checked/)).toHaveClass("integrity", "ok");
  });

  it("warns, with the entry number, when the chain is broken", async () => {
    mockApi({ ok: false, checked: 6, pre_chain_rows: 0, broken_at: 7, reason: "the entry was altered" });
    renderActivity();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Log integrity broken at entry 7: the entry was altered.");
  });
});
