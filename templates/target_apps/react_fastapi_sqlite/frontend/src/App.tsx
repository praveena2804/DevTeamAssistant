import { useEffect, useState } from "react";
import { fetchHealth, type HealthStatus } from "./api/health";

const LABELS: Record<HealthStatus, string> = {
  ok: "Backend is running",
  degraded: "Backend answered with a problem",
  unreachable: "Backend is not reachable",
};

export function StatusBadge({ status }: { status: HealthStatus | null }) {
  return <p role="status">{status === null ? "Checking the backend…" : LABELS[status]}</p>;
}

export function App() {
  const [status, setStatus] = useState<HealthStatus | null>(null);
  useEffect(() => {
    let active = true;
    void fetchHealth().then((value) => {
      if (active) {
        setStatus(value);
      }
    });
    return () => {
      active = false;
    };
  }, []);
  return (
    <main>
      <h1>{"{{APP_TITLE}}"}</h1>
      <StatusBadge status={status} />
    </main>
  );
}
