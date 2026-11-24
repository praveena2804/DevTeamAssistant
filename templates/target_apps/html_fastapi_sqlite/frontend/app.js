// Starter-kit script: shows whether the backend answers /health. Feature scripts are added next to it.
"use strict";

const LABELS = {
  ok: "Backend is running",
  degraded: "Backend answered with a problem",
  unreachable: "Backend is not reachable",
};

async function checkHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) {
      return "degraded";
    }
    const body = await response.json();
    return body.status === "ok" ? "ok" : "degraded";
  } catch {
    return "unreachable";
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const status = await checkHealth();
  document.getElementById("status").textContent = LABELS[status];
});
