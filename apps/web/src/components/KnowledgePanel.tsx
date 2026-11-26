import { useState } from "react";

import { ApiError, getIndexStatus, requestIndexRebuild } from "../api/client";
import { useLoad } from "../api/useLoad";

/**
 * Part F: is the project's knowledge indexed for retrieval? The worker indexes documents after upload; the
 * page only reads the status and can queue a rebuild (the API never embeds or searches, D-57).
 */
export function KnowledgePanel({ projectId }: { projectId: string }) {
  const [state, reload] = useLoad((signal) => getIndexStatus(projectId, signal), [projectId]);
  const [error, setError] = useState<string | null>(null);

  async function rebuild() {
    try {
      await requestIndexRebuild(projectId);
      setError(null);
      reload();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The rebuild could not be requested.");
    }
  }

  if (state.kind === "loading") return <p role="status">Loading search index…</p>;
  if (state.kind === "error") return <p role="alert">{state.error.message}</p>;
  const status = state.data;
  const job = status.latest_job;
  return (
    <section aria-labelledby="knowledge-title">
      <h2 id="knowledge-title">Search index</h2>
      {error && <p role="alert">{error}</p>}
      <p data-testid="index-line">
        {status.index_complete
          ? `All ${status.documents_extracted} document(s) indexed with ${status.embedding_model ?? "?"}.`
          : `${status.documents_indexed} of ${status.documents_extracted} document(s) indexed.`}
        {job ? ` Last job: ${job.kind} ${job.status}${job.error_code ? ` (${job.error_code})` : ""}.` : ""}
      </p>
      <button
        type="button"
        disabled={job?.status === "queued" || job?.status === "running"}
        onClick={() => void rebuild()}
      >
        Rebuild index
      </button>
    </section>
  );
}
