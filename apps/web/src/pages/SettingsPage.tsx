import {
  getEvalStatus,
  getLimitSettings,
  getModelSettings,
  getProfileSettings,
  type LimitsSettings,
  type ModelsSettings,
  type ProfileSettings,
  type PromptStatus,
} from "../api/client";
import { useLoad } from "../api/useLoad";
import { CatalogPanel } from "../components/CatalogPanel";

/**
 * Part F: read-only platform settings (D-55). Models and routing come from config/models.yaml; whether a
 * model is installed (and its digest) is what the worker last saw in the local Ollama. Nothing here changes
 * configuration: settings stay in files under version control.
 */
type Loaded = { models: ModelsSettings; profile: ProfileSettings; limits: LimitsSettings; evals: PromptStatus[] };

async function loadAll(signal: AbortSignal): Promise<Loaded> {
  const [models, profile, limits, evals] = await Promise.all([
    getModelSettings(signal),
    getProfileSettings(signal),
    getLimitSettings(signal),
    getEvalStatus(signal),
  ]);
  return { models, profile, limits, evals };
}

function installedText(installed: boolean | null | undefined): string {
  if (installed === null || installed === undefined) return "not checked yet (start the worker)";
  return installed ? "installed" : "NOT installed";
}

export function SettingsPage() {
  const [state, reload] = useLoad(loadAll, []);
  if (state.kind === "error") return <p role="alert">{state.error.message}</p>;
  if (state.kind !== "loaded") return <p>Loading…</p>;
  const { models, profile, limits, evals } = state.data;
  return (
    <section aria-labelledby="settings-title">
      <h1 id="settings-title">Settings</h1>
      <p>
        Read-only. Models run only on this computer through the local Ollama. To change a setting, edit the files
        in <code>config/</code>.
      </p>
      <button type="button" onClick={reload}>
        Refresh
      </button>
      <h2>Models</h2>
      <p data-testid="models-line">
        Ollama {models.ollama_version ?? "version unknown"}; models stay loaded for {models.keep_alive}; answer
        cache {models.response_cache ? "on" : "off"}.
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Kind</th>
            <th scope="col">Approved</th>
            <th scope="col">Peak memory</th>
            <th scope="col">On this computer</th>
            <th scope="col">Digest</th>
          </tr>
        </thead>
        <tbody>
          {models.models.map((m) => (
            <tr key={m.tag}>
              <td>{m.tag}</td>
              <td>{m.kind}</td>
              <td>{m.approved ? "yes" : "no"}</td>
              <td>{m.peak_ram_mb !== null && m.peak_ram_mb !== undefined ? `${m.peak_ram_mb} MB` : "—"}</td>
              <td>{installedText(m.installed)}</td>
              <td>{m.digest ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Slots</h3>
      <ul>
        {models.slots.map((s) => (
          <li key={s.slot}>
            {s.slot}: {s.model ?? "not configured"}
            {s.fallbacks.length > 0 ? ` (if it does not fit: ${s.fallbacks.join(", ")})` : ""}
          </li>
        ))}
      </ul>
      <h2>Hardware profile</h2>
      <p data-testid="profile-line">
        Profile {profile.profile}: models up to {profile.max_model_ram_mb} MB, at least {profile.min_free_ram_mb} MB
        free before a call, sandbox containers {profile.sandbox_memory_mb} MB / {profile.sandbox_cpus} CPUs.
      </p>
      <h2>Limits</h2>
      <pre>{JSON.stringify({ rate_limits: limits.rate_limits, rag: limits.rag, context: limits.context }, null, 2)}</pre>
      <h2>Prompt evaluations</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Prompt</th>
            <th scope="col">Active version</th>
            <th scope="col">Evaluation required</th>
            <th scope="col">Report</th>
          </tr>
        </thead>
        <tbody>
          {evals.map((e) => (
            <tr key={e.prompt_id}>
              <td>{e.prompt_id}</td>
              <td>v{e.active_version}</td>
              <td>{e.eval_required ? "yes" : "not yet (stub until its agent part)"}</td>
              <td>{e.report}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <CatalogPanel />
    </section>
  );
}
