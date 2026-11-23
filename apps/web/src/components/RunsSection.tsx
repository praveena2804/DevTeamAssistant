import { useState } from "react";
import { Link } from "react-router-dom";

import { listRuns, startRun } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "./ErrorNotice";

/** Runs of one project: start a Part D trial run and open any run's progress page (Part D, LG-08). */
export function RunsSection({ projectId, hasDocuments }: { projectId: string; hasDocuments: boolean }) {
  const [runs, reload] = useLoad((signal) => listRuns(projectId, signal), [projectId]);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  async function onStart() {
    setBusy(true);
    setError(null);
    try {
      await startRun(projectId);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err : new Error("The run could not be started."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h2>Runs</h2>
      <p className="muted">
        A Part D trial run uses the local AI model for each agent step. Services that arrive later (building,
        testing, packaging, publishing) are stand-ins that run nothing. Nothing is built, tested or verified.
      </p>
      <button type="button" onClick={() => void onStart()} disabled={busy || !hasDocuments}>
        {busy ? "Starting…" : "Start trial run"}
      </button>
      {!hasDocuments && <p className="muted">Upload a BRD first.</p>}
      {error && <ErrorNotice error={error} />}
      {runs.kind === "error" && <ErrorNotice error={runs.error} />}
      {runs.kind === "loaded" && runs.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Stage</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.map((run) => (
              <tr key={run.id}>
                <td>
                  <Link to={`/projects/${projectId}/runs/${run.id}`}>{run.id}</Link>
                </td>
                <td>{run.status.replaceAll("_", " ")}</td>
                <td>{run.stage}</td>
                <td>{new Date(run.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
