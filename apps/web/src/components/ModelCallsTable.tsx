import { listModelCalls } from "../api/client";
import { useLoad } from "../api/useLoad";

/** Part F: every model call of a run (AC-F-01), metadata only: never prompts, inputs or answers. */
export function ModelCallsTable({ projectId, runId }: { projectId: string; runId: string }) {
  const [calls] = useLoad((signal) => listModelCalls(projectId, runId, signal), [projectId, runId]);
  if (calls.kind === "loading") return <p role="status">Loading model calls…</p>;
  if (calls.kind === "error") return <p role="alert">{calls.error.message}</p>;
  return (
    <section aria-labelledby="model-calls-title">
      <h2 id="model-calls-title">Model calls</h2>
      {calls.data.length === 0 ? (
        <p>No model calls recorded for this run.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Operation</th>
              <th>Model</th>
              <th>Chosen by</th>
              <th>Recovery</th>
              <th>Tokens</th>
              <th>Time</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {calls.data.map((c) => (
              <tr key={c.call_id} className={c.outcome === "error" ? "bad" : undefined}>
                <td>{c.operation_id}</td>
                <td>
                  {c.model_tag}
                  {c.model_digest ? ` (${c.model_digest})` : ""}
                  {c.cache_hit ? " — cached" : ""}
                </td>
                <td>{c.route_rule}</td>
                <td>{c.recovery_step === "none" ? "—" : c.recovery_step}</td>
                <td>
                  {c.input_tokens} in / {c.output_tokens} out
                </td>
                <td>{Math.round(c.duration_ms / 100) / 10} s</td>
                <td>{c.error_code ? `${c.outcome} (${c.error_code})` : c.outcome}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
