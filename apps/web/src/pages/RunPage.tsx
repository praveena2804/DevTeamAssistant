import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  type GateInfo,
  type RunDetail,
  type RunEvent,
  controlRun,
  decideGate,
  getGate,
  getRun,
  listRunEvents,
  listRunTasks,
  runEventsStreamUrl,
} from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";

const TERMINAL = new Set(["completed", "cancelled", "failed", "blocked"]);
const GATE_TITLES: Record<string, string> = {
  feasibility_decision: "Feasibility decision",
  project_approval: "Approve the project understanding",
  publish_approval: "Publish decision",
};

function describe(event: RunEvent): string {
  const p = event.payload as Record<string, unknown>;
  switch (event.kind) {
    case "node.started":
      return `Started ${String(p.node).replaceAll("_", " ")}`;
    case "node.finished":
      return `Finished ${String(p.node).replaceAll("_", " ")} (${String(p.outcome)}, ${String(p.duration_ms)} ms)`;
    case "gate.opened":
      return `Waiting for your decision: ${GATE_TITLES[String(p.gate)] ?? String(p.gate)}`;
    case "service.recorded":
      return `${String(p.node).replaceAll("_", " ")}: ${String(p.status).replaceAll("_", " ")} (stand-in, arrives in Part ${String(p.available_in_part)})`;
    case "run.summary":
      return `Final report: ${String(p.completed_tasks)} of ${String(p.total_tasks)} tasks completed, ${String(p.model_calls)} local model calls. Not verified.`;
    case "metric.sampled":
      return `${String(p.metric).replaceAll("_", " ")}: ${String(p.value)}`;
    default:
      return event.kind;
  }
}

/**
 * Live progress of one run: events arrive over server-sent events (with replay from the last seen event);
 * if the stream is unavailable the page falls back to polling the JSON event list.
 */
function useRunEvents(projectId: string, runId: string, finished: boolean): RunEvent[] {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const last = useRef(0);

  useEffect(() => {
    let stopped = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const add = (incoming: RunEvent[]) => {
      const fresh = incoming.filter((e) => e.seq > last.current);
      if (fresh.length === 0) return;
      last.current = fresh[fresh.length - 1]?.seq ?? last.current;
      setEvents((prev) => [...prev, ...fresh]);
    };
    const poll = async () => {
      try {
        add(await listRunEvents(projectId, runId, last.current));
      } catch {
        // keep polling; the page shows the run status separately
      }
      if (!stopped && !finished) timer = setTimeout(() => void poll(), 2000);
    };
    if (typeof EventSource !== "undefined" && !finished) {
      source = new EventSource(runEventsStreamUrl(projectId, runId, last.current));
      source.onmessage = () => undefined;
      const handler = (msg: MessageEvent<string>) => {
        const payload = JSON.parse(msg.data) as Record<string, unknown>;
        add([{ seq: Number(msg.lastEventId), kind: msg.type, ts: String(payload.ts ?? ""), payload }]);
      };
      for (const kind of ["node.started", "node.finished", "gate.opened", "service.recorded", "run.summary", "metric.sampled"]) {
        source.addEventListener(kind, handler as EventListener);
      }
      source.addEventListener("end", () => source?.close());
      source.onerror = () => {
        source?.close();
        source = null;
        void poll();
      };
    } else {
      void poll();
    }
    return () => {
      stopped = true;
      source?.close();
      if (timer) clearTimeout(timer);
    };
  }, [projectId, runId, finished]);

  return events;
}

function GatePanel({ projectId, run, onDecided }: { projectId: string; run: RunDetail; onDecided: () => void }) {
  const [gate] = useLoad((signal) => getGate(projectId, run.id, signal), [projectId, run.id, run.pending_gate]);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  async function decide(info: GateInfo, decision: "approved" | "rejected") {
    setBusy(true);
    setError(null);
    try {
      await decideGate(projectId, run.id, info.gate, decision);
      onDecided();
    } catch (err) {
      setError(err instanceof Error ? err : new Error("The decision was not recorded."));
    } finally {
      setBusy(false);
    }
  }

  if (gate.kind === "loading") return <p role="status">Loading the decision…</p>;
  if (gate.kind === "error") return <ErrorNotice error={gate.error} />;
  const info = gate.data;
  const notes = info.notes ?? [];
  const requirements = info.requirements ?? [];
  return (
    <section className="notice" aria-label="Decision needed">
      <h2>{GATE_TITLES[info.gate] ?? info.gate}</h2>
      {notes.map((note) => (
        <p key={note}>{note}</p>
      ))}
      {requirements.length > 0 && (
        <>
          <h3>Requirements in this specification</h3>
          <ul>
            {requirements.map((r) => (
              <li key={r.key}>
                <strong>{r.key}</strong>: {r.description}
              </li>
            ))}
          </ul>
        </>
      )}
      {info.spec_checksum && <p className="muted">Specification fingerprint: {info.spec_checksum.slice(0, 16)}…</p>}
      <button type="button" onClick={() => void decide(info, "approved")} disabled={busy}>
        Approve
      </button>{" "}
      <button type="button" onClick={() => void decide(info, "rejected")} disabled={busy}>
        Reject
      </button>
      {error && <ErrorNotice error={error} />}
    </section>
  );
}

export function RunPage() {
  const { projectId = "", runId = "" } = useParams();
  const [tick, setTick] = useState(0);
  const [run] = useLoad((signal) => getRun(projectId, runId, signal), [projectId, runId, tick]);
  const finished = run.kind === "loaded" && TERMINAL.has(run.data.status);
  const events = useRunEvents(projectId, runId, finished);
  const [tasks] = useLoad((signal) => listRunTasks(projectId, runId, signal), [projectId, runId, tick, events.length]);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (finished) return;
    const timer = setInterval(() => setTick((t) => t + 1), 4000);
    return () => clearInterval(timer);
  }, [finished]);

  async function control(action: "pause" | "resume" | "cancel") {
    setError(null);
    try {
      await controlRun(projectId, runId, action);
      setTick((t) => t + 1);
    } catch (err) {
      setError(err instanceof Error ? err : new Error("The request failed."));
    }
  }

  if (run.kind === "loading") return <p role="status">Loading run…</p>;
  if (run.kind === "error") return <ErrorNotice error={run.error} />;
  const r = run.data;
  const progress = events.filter((e) => e.kind !== "metric.sampled");

  return (
    <section>
      <p>
        <Link to={`/projects/${projectId}`}>← Project</Link> ·{" "}
        <Link to={`/projects/${projectId}/runs/${runId}/trace`}>Trace</Link> ·{" "}
        <Link to={`/projects/${projectId}/runs/${runId}/history`}>History</Link>
      </p>
      <p className="notice" role="note">
        {r.label}
      </p>
      <h1>Build progress</h1>
      <dl className="facts">
        <dt>Status</dt>
        <dd data-testid="run-status">{r.status.replaceAll("_", " ")}</dd>
        <dt>Stage</dt>
        <dd>{r.stage}</dd>
        <dt>Current step</dt>
        <dd>{r.current_node ? r.current_node.replaceAll("_", " ") : "–"}</dd>
        <dt>Tasks</dt>
        <dd>
          {r.completed_tasks} of {r.total_tasks} completed
        </dd>
        {r.blocker && (
          <>
            <dt>Reason</dt>
            <dd>{r.blocker}</dd>
          </>
        )}
      </dl>
      {!finished && (
        <p>
          {r.status === "running" && (
            <button type="button" onClick={() => void control("pause")}>
              Pause
            </button>
          )}{" "}
          {(r.status === "paused" || r.status === "waiting_for_resources") && (
            <button type="button" onClick={() => void control("resume")}>
              Resume
            </button>
          )}{" "}
          <button type="button" onClick={() => void control("cancel")}>
            Cancel run
          </button>
        </p>
      )}
      {error && <ErrorNotice error={error} />}
      {r.status === "waiting_for_human" && r.pending_gate && (
        <GatePanel projectId={projectId} run={r} onDecided={() => setTick((t) => t + 1)} />
      )}

      <h2>Tasks</h2>
      {tasks.kind === "loaded" && tasks.data.length === 0 && <p>No tasks yet.</p>}
      {tasks.kind === "loaded" && tasks.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Agent output</th>
              <th>Harness verified</th>
              <th>Files</th>
            </tr>
          </thead>
          <tbody>
            {tasks.data.map((t) => (
              <tr key={t.task_id} className={t.status === "failed" || t.status === "blocked" ? "bad" : undefined}>
                <td>{t.agent.replaceAll("_", " ")}</td>
                <td>{t.status}</td>
                <td>
                  {t.attempts} / {t.max_attempts}
                </td>
                <td>{t.agent_returned_output ? "returned" : "–"}</td>
                <td>no (nothing is verified in Part D)</td>
                <td>{t.files.map((f) => f.path).join(", ") || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Progress</h2>
      <ol className="events" aria-live="polite">
        {progress.map((e) => (
          <li key={e.seq}>{describe(e)}</li>
        ))}
      </ol>
    </section>
  );
}
