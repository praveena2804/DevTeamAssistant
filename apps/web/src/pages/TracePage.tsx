import { Link, useParams } from "react-router-dom";

import { type Span, getRunTrace } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";
import { ModelCallsTable } from "../components/ModelCallsTable";

function tree(spans: readonly Span[]): { span: Span; depth: number }[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const span of [...spans].sort((a, b) => a.start_ts.localeCompare(b.start_ts))) {
    if (span.parent_span_id && ids.has(span.parent_span_id)) {
      children.set(span.parent_span_id, [...(children.get(span.parent_span_id) ?? []), span]);
    } else {
      roots.push(span);
    }
  }
  const out: { span: Span; depth: number }[] = [];
  const walk = (span: Span, depth: number) => {
    out.push({ span, depth });
    for (const child of children.get(span.span_id) ?? []) walk(child, depth + 1);
  };
  roots.forEach((root) => walk(root, 0));
  return out;
}

/** Trace viewer (HC-11): each step's span, its duration and outcome. Attributes are allowlisted IDs only. */
export function TracePage() {
  const { projectId = "", runId = "" } = useParams();
  const [trace] = useLoad((signal) => getRunTrace(projectId, runId, signal), [projectId, runId]);
  if (trace.kind === "loading") return <p role="status">Loading trace…</p>;
  if (trace.kind === "error") return <ErrorNotice error={trace.error} />;
  const rows = tree(trace.data.spans);
  return (
    <section>
      <p>
        <Link to={`/projects/${projectId}/runs/${runId}`}>← Build progress</Link>
      </p>
      <h1>Trace</h1>
      <p className="muted">Stored locally only. Document text, prompts and model answers are never recorded here.</p>
      {rows.length === 0 ? (
        <p>No steps have been traced yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Step</th>
              <th>Duration</th>
              <th>Outcome</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ span, depth }) => (
              <tr key={span.span_id} className={span.status === "error" ? "bad" : undefined}>
                <td style={{ paddingLeft: `${depth * 1.25}rem` }}>{span.name}</td>
                <td>{span.duration_ms} ms</td>
                <td>{String(span.attributes.outcome ?? span.attributes.disposition ?? span.status)}</td>
                <td className="muted">
                  {["agent.role", "operation_id", "model.id", "tokens.input", "tokens.output", "tool.name", "error.code"]
                    .filter((key) => span.attributes[key] !== undefined)
                    .map((key) => `${key}=${String(span.attributes[key])}`)
                    .join(" ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ModelCallsTable projectId={projectId} runId={runId} />
    </section>
  );
}
