import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getDocument, listChunks } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";

export const PAGE_SIZE = 20;

/** Extraction review (AC-B-01): every piece of text with its source reference. */
export function ExtractionReviewPage() {
  const { projectId = "", documentId = "" } = useParams();
  const [offset, setOffset] = useState(0);
  const [doc] = useLoad((signal) => getDocument(projectId, documentId, signal), [projectId, documentId]);
  const [chunks] = useLoad(
    (signal) => listChunks(projectId, documentId, offset, PAGE_SIZE, signal),
    [projectId, documentId, offset],
  );

  if (doc.kind === "loading") return <p role="status">Loading document…</p>;
  if (doc.kind === "error") return <ErrorNotice error={doc.error} />;
  const d = doc.data;

  return (
    <section>
      <p>
        <Link to={`/projects/${projectId}`}>← Back to project</Link>
      </p>
      <h1>{d.original_filename}</h1>
      <dl>
        <dt>Role</dt>
        <dd>{d.role === "primary_brd" ? "Main BRD" : "Supporting document"}</dd>
        <dt>Pages</dt>
        <dd>{d.page_count ?? "Not applicable (this format has no fixed pages)"}</dd>
        <dt>Pieces of text</dt>
        <dd>{d.chunk_count}</dd>
        <dt>Checksum (SHA-256)</dt>
        <dd>
          <code>{d.sha256}</code>
        </dd>
      </dl>

      {d.warnings.length > 0 && (
        <div className="notice">
          <h2>Warnings</h2>
          <ul>
            {d.warnings.map((w, i) => (
              <li key={`${w.code}-${i}`}>{w.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="review">
        <nav aria-label="Document outline" className="outline">
          <h2>Outline</h2>
          {d.outline.length === 0 ? (
            <p>No headings found.</p>
          ) : (
            <ul>
              {d.outline.map((item, i) => (
                <li key={`${item.title}-${i}`} style={{ marginLeft: `${Math.max(item.level - 1, 0)}rem` }}>
                  {item.title}
                  {item.page ? <span className="muted"> (p.{item.page})</span> : null}
                </li>
              ))}
            </ul>
          )}
        </nav>

        <div className="chunks">
          <h2>Extracted text</h2>
          {chunks.kind === "loading" && <p role="status">Loading text…</p>}
          {chunks.kind === "error" && <ErrorNotice error={chunks.error} />}
          {chunks.kind === "loaded" && (
            <>
              <p className="muted">
                Showing {chunks.data.total === 0 ? 0 : offset + 1}–{offset + chunks.data.items.length} of{" "}
                {chunks.data.total}
              </p>
              {chunks.data.items.map((chunk) => (
                <article key={chunk.id} className="chunk">
                  <header>
                    <span className="reference">{chunk.reference}</span>
                    {chunk.kind === "table" && <span className="badge">table</span>}
                  </header>
                  <pre>{chunk.text}</pre>
                </article>
              ))}
              <div className="pager">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  Previous
                </button>
                <button
                  type="button"
                  disabled={offset + PAGE_SIZE >= chunks.data.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
