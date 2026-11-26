import { getCatalog } from "../api/client";
import { useLoad } from "../api/useLoad";

const EFFECTIVE_TEXT: Record<string, string> = {
  available: "available",
  available_limited: "available (limited)",
  demoted: "DEMOTED",
  unsupported: "not supported",
  later_change_request: "later, as a change request",
};

/**
 * Part G: the Capability Catalog, read-only (D-55). The declared status comes from
 * capability_catalog/capabilities.yaml; the effective status is computed now from the starter kits, the
 * registered recipes and the latest template verifications, so a kit that was never verified, failed or
 * went stale is shown as demoted, with the reason.
 */
export function CatalogPanel() {
  const [state] = useLoad((signal) => getCatalog(signal), []);
  if (state.kind === "loading") return <p role="status">Loading the capability catalog…</p>;
  if (state.kind === "error") return <p role="alert">{state.error.message}</p>;
  const catalog = state.data;
  return (
    <section aria-labelledby="catalog-title">
      <h2 id="catalog-title">Capability Catalog</h2>
      <p data-testid="catalog-line">
        {catalog.entries.length} entries; {catalog.demoted} demoted. Catalog {catalog.catalog_hash.slice(0, 12)}.
        &quot;Available&quot; means a verified starter kit and its recipes exist; generating code for it arrives
        with the developer agents (Parts K–L).
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Capability</th>
            <th scope="col">Category</th>
            <th scope="col">Declared</th>
            <th scope="col">Effective</th>
            <th scope="col">Reason</th>
            <th scope="col">Alternatives</th>
          </tr>
        </thead>
        <tbody>
          {catalog.entries.map((e) => (
            <tr key={e.key} data-testid={`catalog-${e.key}`}>
              <td>{e.display_name}</td>
              <td>{e.category}</td>
              <td>{e.declared_status.replaceAll("_", " ")}</td>
              <td>{EFFECTIVE_TEXT[e.effective_status] ?? e.effective_status}</td>
              <td>{e.reason ?? "—"}</td>
              <td>{e.substitutions.length > 0 ? e.substitutions.join(", ") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
