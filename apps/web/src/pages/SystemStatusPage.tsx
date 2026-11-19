import { useEffect, useState } from "react";

import { getHealth, getVersion, type HealthResponse, type VersionResponse } from "../api/client";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; health: HealthResponse; version: VersionResponse | null };

export function SystemStatusPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getHealth(controller.signal), getVersion(controller.signal).catch(() => null)])
      .then(([health, version]) => setState({ kind: "loaded", health, version }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: error instanceof Error ? error.message : "Unknown error" });
      });
    return () => controller.abort();
  }, []);

  if (state.kind === "loading") {
    return <p role="status">Checking platform status…</p>;
  }
  if (state.kind === "error") {
    return (
      <section>
        <h1>System status</h1>
        <p role="alert">The platform API is unreachable: {state.message}</p>
      </section>
    );
  }

  const { health, version } = state;
  return (
    <section>
      <h1>System status</h1>
      <p>
        Overall: <strong data-testid="overall-status">{health.status === "ok" ? "Healthy" : "Degraded"}</strong>
      </p>
      <table>
        <caption>Component checks</caption>
        <thead>
          <tr>
            <th scope="col">Component</th>
            <th scope="col">Status</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(health.checks).map(([name, check]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{check.status}</td>
              <td>{check.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {version && (
        <dl>
          <dt>Version</dt>
          <dd>{version.version}</dd>
          <dt>Profile</dt>
          <dd>{version.profile}</dd>
          <dt>Schema revision</dt>
          <dd>{version.schema_revision ?? "not migrated"}</dd>
        </dl>
      )}
    </section>
  );
}
