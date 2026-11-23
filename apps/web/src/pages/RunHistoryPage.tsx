import { Link, useParams } from "react-router-dom";

import { getRunHistory, getRunMetrics } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";

/** Run history (HC-21): every recorded status change and decision for this run, plus run metrics. */
export function RunHistoryPage() {
  const { projectId = "", runId = "" } = useParams();
  const [history] = useLoad((signal) => getRunHistory(projectId, runId, signal), [projectId, runId]);
  const [metrics] = useLoad((signal) => getRunMetrics(projectId, runId, signal), [projectId, runId]);

  return (
    <section>
      <p>
        <Link to={`/projects/${projectId}/runs/${runId}`}>← Build progress</Link>
      </p>
      <h1>Run history</h1>
      {metrics.kind === "error" && <ErrorNotice error={metrics.error} />}
      {metrics.kind === "loaded" && (
        <dl className="facts">
          <dt>Local model calls</dt>
          <dd>{metrics.data.model_calls}</dd>
          <dt>Tokens (in / out)</dt>
          <dd>
            {metrics.data.input_tokens} / {metrics.data.output_tokens}
          </dd>
          <dt>Model time</dt>
          <dd>{Math.round(metrics.data.model_ms / 1000)} s</dd>
          {Object.entries(metrics.data.latest_samples).map(([name, value]) => (
            <div key={name} style={{ display: "contents" }}>
              <dt>{name.replaceAll("_", " ")}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {history.kind === "loading" && <p role="status">Loading history…</p>}
      {history.kind === "error" && <ErrorNotice error={history.error} />}
      {history.kind === "loaded" && (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>What</th>
              <th>Outcome</th>
              <th>By</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((item, index) => (
              <tr key={`${item.ts}-${index}`} className={item.outcome === "success" ? undefined : "bad"}>
                <td>{new Date(item.ts).toLocaleString()}</td>
                <td>{item.action}</td>
                <td>{item.outcome}</td>
                <td>{item.principal_id ?? "–"}</td>
                <td className="muted">
                  {Object.entries(item.details)
                    .filter(([, value]) => typeof value !== "object")
                    .map(([key, value]) => `${key}=${String(value)}`)
                    .join(" ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
