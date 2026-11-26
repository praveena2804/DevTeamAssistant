import type { FeasibilityItem } from "../api/client";

const STATUS_ORDER = ["not_supported", "supported_with_substitution", "partial", "supported"];
const STATUS_TEXT: Record<string, string> = {
  supported: "supported",
  supported_with_substitution: "supported with substitution",
  partial: "partial",
  not_supported: "NOT supported",
};

/** Part G: one row per technology, integration, login method or deployment target, worst status first. */
export function FeasibilityTable({ items }: { items: FeasibilityItem[] }) {
  if (items.length === 0) return <p>No items.</p>;
  const ordered = [...items].sort(
    (a, b) =>
      STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || a.subject_name.localeCompare(b.subject_name),
  );
  return (
    <table>
      <thead>
        <tr>
          <th scope="col">Subject</th>
          <th scope="col">Catalog key</th>
          <th scope="col">Status</th>
          <th scope="col">Reason</th>
          <th scope="col">Alternative</th>
          <th scope="col">Affected requirements</th>
        </tr>
      </thead>
      <tbody>
        {ordered.map((i) => (
          <tr key={i.item_id} data-testid={`item-${i.subject_name}`}>
            <td>
              {i.subject_name}
              {i.source ? <span className="muted"> ({i.source})</span> : null}
            </td>
            <td>{i.catalog_key ?? "not in the catalog"}</td>
            <td>{STATUS_TEXT[i.status] ?? i.status}</td>
            <td>{i.reason ?? "—"}</td>
            <td>
              {i.alternative
                ? `${i.alternative} (${i.substitution === "approved" ? "approved" : "offered — not applied"})`
                : "—"}
            </td>
            <td>
              {i.whole_application
                ? `all in-scope requirements (${i.requirement_ids.length})`
                : i.requirement_ids.join(", ") || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
