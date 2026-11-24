import { useState } from "react";

import {
  ApiError,
  getSandboxStatus,
  listStarterKits,
  requestVerification,
  type SandboxStatus,
  type StarterKit,
  type Verification,
} from "../api/client";
import { useLoad } from "../api/useLoad";

/**
 * Part E: the Docker sandbox and the two starter kits. The page only reads status and records and queues
 * verification requests; the worker runs them offline in the sandbox. "Passed" is shown only when every
 * recipe completed and every test recipe really ran tests without failures (the record enforces this).
 */
const STATUS_TEXT: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  passed: "Passed",
  failed: "Failed",
  blocked: "Blocked",
  stale: "Out of date (the kit or image changed)",
};

function engineLine(status: SandboxStatus | null): string {
  if (status === null) return "Loading…";
  if (!status.configured) return "Sandbox disabled in configuration: sandbox work is Blocked.";
  if (status.reachable === null || status.reachable === undefined) return "Not checked yet (start the worker).";
  if (!status.reachable) return `Not usable: ${status.reason ?? "unknown reason"}. Sandbox work is Blocked.`;
  return `Docker Engine ${status.engine_version ?? ""} on ${status.operating_system ?? "WSL"} (mutual TLS)`;
}

function VerificationSummary({ verification }: { verification: Verification | null | undefined }) {
  if (!verification) return <p>Never verified.</p>;
  const seconds = verification.duration_ms === null ? null : Math.round(verification.duration_ms / 1000);
  return (
    <div>
      <p>
        <strong>{STATUS_TEXT[verification.status] ?? verification.status}</strong>
        {seconds !== null ? ` in ${seconds} s` : ""}
        {verification.peak_memory_mb !== null ? `, peak memory ${verification.peak_memory_mb} MB` : ""}
        {verification.reason ? ` — ${verification.reason}` : ""}
      </p>
      {verification.recipes.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Recipe</th>
              <th scope="col">Result</th>
              <th scope="col">Tests</th>
              <th scope="col">Time</th>
            </tr>
          </thead>
          <tbody>
            {verification.recipes.map((recipe) => (
              <tr key={recipe.recipe}>
                <td>{recipe.recipe}</td>
                <td>{recipe.status}</td>
                <td>
                  {recipe.tests
                    ? `${recipe.tests.passed} passed, ${recipe.tests.failed} failed, ${recipe.tests.errors} errors`
                    : "—"}
                </td>
                <td>{Math.round(recipe.duration_ms / 100) / 10} s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

async function loadAll(signal: AbortSignal): Promise<{ status: SandboxStatus; kits: StarterKit[] }> {
  const [status, kits] = await Promise.all([getSandboxStatus(signal), listStarterKits(signal)]);
  return { status, kits };
}

export function SandboxPage() {
  const [state, reload] = useLoad(loadAll, []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const status = state.kind === "loaded" ? state.data.status : null;
  const kits = state.kind === "loaded" ? state.data.kits : [];

  async function verify(templateId: string) {
    setBusy(templateId);
    try {
      await requestVerification(templateId);
      setError(null);
      reload();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The verification could not be requested.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="sandbox-title">
      <h1 id="sandbox-title">Sandbox and starter kits</h1>
      <p>
        Generated code runs only in throw-away Docker containers without network access. If the engine is
        not usable, the work is Blocked; nothing runs on this computer instead.
      </p>
      {error && <p role="alert">{error}</p>}
      {state.kind === "error" && <p role="alert">{state.error.message}</p>}
      <button type="button" onClick={reload}>
        Refresh
      </button>
      <h2>Engine</h2>
      <p data-testid="engine-line">{engineLine(status)}</p>
      {status && status.images.length > 0 && (
        <ul>
          {status.images.map((image) => (
            <li key={image.name}>
              {image.tag} ({image.image_id}), vulnerability audit: {image.audit}
            </li>
          ))}
        </ul>
      )}
      <h2>Starter kits</h2>
      {kits.map((kit) => {
        const active = kit.latest?.status === "queued" || kit.latest?.status === "running";
        return (
          <article key={kit.template_id} aria-labelledby={`kit-${kit.template_id}`}>
            <h3 id={`kit-${kit.template_id}`}>
              {kit.template_id} <small>v{kit.version}</small>
            </h3>
            <p>{kit.description}</p>
            <VerificationSummary verification={kit.latest} />
            <button type="button" disabled={active || busy === kit.template_id} onClick={() => void verify(kit.template_id)}>
              {active ? "Verification in progress" : "Verify"}
            </button>
          </article>
        );
      })}
    </section>
  );
}
