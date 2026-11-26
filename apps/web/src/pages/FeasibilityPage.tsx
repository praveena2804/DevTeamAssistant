import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { decideGate, feasibilityReportUrl, getFeasibility, getRun } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";
import { FeasibilityTable } from "../components/FeasibilityTable";

const RECOMMENDATION: Record<string, string> = {
  proceed: "Proceed: coverage is at or above the threshold.",
  stop_recommended:
    "Stop recommended: coverage is below the threshold. You may still proceed with the buildable scope after confirming.",
  nothing_to_build: "Nothing can be built: proceeding is disabled. Cancel, or revise the BRD and start a new run.",
  not_applicable: "Coverage is not applicable: no functional requirement or business rule was recorded.",
};

/**
 * Part G: the feasibility report of a run and the feasibility decision (D-01, D-63). Proceed continues with the
 * buildable scope only; below the threshold it needs an explicit confirmation, and at "nothing to build" it is
 * not offered. Cancel takes a reason (cancel, or revise: upload a revised BRD and start a new run). Substitutions
 * are only offered here; accepting one happens in the clarification form (Part I).
 */
export function FeasibilityPage() {
  const { projectId = "", runId = "" } = useParams();
  const [tick, setTick] = useState(0);
  const [report] = useLoad((signal) => getFeasibility(projectId, runId, signal), [projectId, runId, tick]);
  const [run] = useLoad((signal) => getRun(projectId, runId, signal), [projectId, runId, tick]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [reason, setReason] = useState<"cancel" | "revise">("cancel");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  if (report.kind === "loading") return <p role="status">Loading the feasibility report…</p>;
  if (report.kind === "error") return <ErrorNotice error={report.error} />;
  const r = report.data;
  const waiting =
    run.kind === "loaded" && run.data.status === "waiting_for_human" && run.data.pending_gate === "feasibility_decision";
  const coverage = r.coverage_pct === null || r.coverage_pct === undefined ? "n/a" : `${r.coverage_pct.toFixed(1)}%`;

  async function decide(decision: "approved" | "rejected") {
    setBusy(true);
    setError(null);
    try {
      await decideGate(
        projectId,
        runId,
        "feasibility_decision",
        decision,
        decision === "approved" ? { acknowledge_low_coverage: acknowledged } : { reason },
      );
      setTick((t) => t + 1);
    } catch (err) {
      setError(err instanceof Error ? err : new Error("The decision was not recorded."));
    } finally {
      setBusy(false);
    }
  }

  const offered = r.items.filter((i) => i.alternative);
  const gaps = r.items.filter((i) => i.gap);
  return (
    <section aria-labelledby="feasibility-title">
      <h1 id="feasibility-title">Feasibility</h1>
      <p>
        <Link to={`/projects/${projectId}/runs/${runId}`}>Back to the run</Link> ·{" "}
        <a href={feasibilityReportUrl(projectId, runId)}>Download FEASIBILITY_REPORT.md</a>
      </p>
      <p className="muted">Checked by the Feasibility Gate (no AI model) against the Capability Catalog.</p>
      <h2>Coverage</h2>
      <p data-testid="coverage-line">
        Buildable in-scope requirements and business rules: {r.numerator} / {r.denominator} = {coverage} (threshold{" "}
        {r.threshold_pct}%).
      </p>
      <p data-testid="recommendation">{RECOMMENDATION[r.recommendation] ?? r.recommendation}</p>
      {!r.needs_identified && (
        <p className="notice" data-testid="no-needs">
          No technology needs identified: nothing was checked against the catalog and nothing is blocked because nothing
          was identified. Technology extraction from the BRD arrives with the BRD Analyst in Part H.
        </p>
      )}
      <h2>Items</h2>
      <FeasibilityTable items={r.items} />
      <h2>Blocked requirements</h2>
      <p data-testid="blocked">{r.blocked_keys.length ? r.blocked_keys.join(", ") : "None."}</p>
      {r.partial_keys.length > 0 && <p>Partial (not counted as buildable): {r.partial_keys.join(", ")}</p>}
      {offered.length > 0 && (
        <>
          <h2>Substitutions</h2>
          <ul>
            {offered.map((i) => (
              <li key={i.item_id}>
                {i.subject_name} → {i.alternative}: offered — accepted in the clarification form (Part I)
              </li>
            ))}
          </ul>
        </>
      )}
      {gaps.length > 0 && (
        <>
          <h2>Questions for the clarification form</h2>
          <ul>
            {gaps.map((i) => (
              <li key={i.item_id}>{i.gap?.description}</li>
            ))}
          </ul>
        </>
      )}
      {r.demoted.length > 0 && (
        <details>
          <summary>{r.demoted.length} catalog entries are demoted right now</summary>
          <ul>
            {r.demoted.map((d) => (
              <li key={d.key}>
                {d.key}: {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {waiting && (
        <section className="notice" aria-label="Feasibility decision">
          <h2>Your decision</h2>
          {r.proceed_allowed ? (
            <>
              {r.needs_acknowledgement && (
                <label>
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(e) => setAcknowledged(e.target.checked)}
                  />{" "}
                  I understand coverage is below the threshold and want to proceed with the buildable scope.
                </label>
              )}
              <p>
                <button
                  type="button"
                  onClick={() => void decide("approved")}
                  disabled={busy || (r.needs_acknowledgement && !acknowledged)}
                >
                  Proceed
                </button>
              </p>
            </>
          ) : (
            <p data-testid="proceed-disabled">Proceeding is not possible: nothing in scope can be built.</p>
          )}
          <p>
            <label>
              Reason{" "}
              <select value={reason} onChange={(e) => setReason(e.target.value as "cancel" | "revise")}>
                <option value="cancel">cancel the run</option>
                <option value="revise">revise the BRD (then start a new run)</option>
              </select>
            </label>{" "}
            <button type="button" onClick={() => void decide("rejected")} disabled={busy}>
              Cancel
            </button>
          </p>
          {error && <ErrorNotice error={error} />}
        </section>
      )}
    </section>
  );
}
