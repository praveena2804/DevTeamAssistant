import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { type AuditEvent, type AuditIntegrity, listAudit, verifyAudit } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";

const PAGE_SIZE = 50;

const OUTCOME_LABEL: Record<string, string> = { success: "Done", denied: "Refused", error: "Failed" };

function describe(event: AuditEvent): string {
  const d = event.details;
  const parts: string[] = [];
  if (typeof d.filename === "string") parts.push(d.filename);
  if (typeof d.name === "string") parts.push(d.name);
  if (typeof d.reason === "string") parts.push(d.reason.replaceAll("_", " "));
  if (typeof d.code === "string") parts.push(d.code);
  return parts.join(" · ");
}

/** Hash-chain check (HC-21): shows whether any entry was changed, removed or inserted. */
export function IntegrityBadge({ report }: { report: AuditIntegrity }) {
  if (report.ok) {
    return (
      <p className="integrity ok" role="status">
        ✓ Log intact: {report.checked + report.pre_chain_rows} entries checked, none changed or removed.
      </p>
    );
  }
  return (
    <p className="integrity broken" role="alert">
      ⚠ Log integrity broken at entry {report.broken_at}: {report.reason}.
    </p>
  );
}

/** Read-only audit trail (HC-21). Rows can never be edited or deleted. */
export function ActivityPage() {
  const { projectId = null } = useParams();
  const [offset, setOffset] = useState(0);
  const [state] = useLoad((signal) => listAudit(projectId, offset, PAGE_SIZE, signal), [projectId, offset]);
  const [integrity] = useLoad((signal) => verifyAudit(signal), [offset]);

  return (
    <section>
      {projectId && (
        <p>
          <Link to={`/projects/${projectId}`}>← Back to project</Link>
        </p>
      )}
      <h1>{projectId ? "Project activity" : "All activity"}</h1>
      <p className="muted">A permanent record of every change and every refused request.</p>
      {integrity.kind === "loaded" && <IntegrityBadge report={integrity.data} />}
      {state.kind === "loading" && <p role="status">Loading activity…</p>}
      {state.kind === "error" && <ErrorNotice error={state.error} />}
      {state.kind === "loaded" && (
        <>
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Result</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {state.data.items.map((event) => (
                <tr key={event.id} className={event.outcome === "success" ? "" : "bad"}>
                  <td>{new Date(event.ts).toLocaleString()}</td>
                  <td>{event.action}</td>
                  <td>{OUTCOME_LABEL[event.outcome] ?? event.outcome}</td>
                  <td>{describe(event)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Newer
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= state.data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Older
            </button>
          </div>
        </>
      )}
    </section>
  );
}
