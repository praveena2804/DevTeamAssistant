import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./App";
import { fetchHealth, statusFrom } from "./api/health";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("health status", () => {
  it("maps backend replies", () => {
    expect(statusFrom({ status: "ok" })).toBe("ok");
    expect(statusFrom({ status: "degraded" })).toBe("degraded");
    expect(statusFrom(null)).toBe("unreachable");
  });

  it("reports an unreachable backend without throwing", async () => {
    const failing = (() => Promise.reject(new Error("offline"))) as unknown as typeof fetch;
    await expect(fetchHealth(failing)).resolves.toBe("unreachable");
  });

  it("renders the status badge", () => {
    const host = document.createElement("div");
    act(() => {
      createRoot(host).render(<StatusBadge status="ok" />);
    });
    expect(host.textContent).toBe("Backend is running");
  });
});
