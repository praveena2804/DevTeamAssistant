import { type ChangeEvent, type DragEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { type UploadResponse, getProject, listDocuments, uploadDocuments } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";

const ACCEPT = ".pdf,.docx,.txt,.md";

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function ProjectPage() {
  const { projectId = "" } = useParams();
  const [project] = useLoad((signal) => getProject(projectId, signal), [projectId]);
  const [documents, reloadDocuments] = useLoad((signal) => listDocuments(projectId, signal), [projectId]);
  const [selected, setSelected] = useState<File[]>([]);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  function choose(files: FileList | null) {
    setSelected(files ? Array.from(files) : []);
    setResult(null);
    setError(null);
  }

  async function onUpload() {
    setBusy(true);
    setError(null);
    try {
      setResult(await uploadDocuments(projectId, selected));
      setSelected([]);
      reloadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Upload failed."));
    } finally {
      setBusy(false);
    }
  }

  if (project.kind === "loading") return <p role="status">Loading project…</p>;
  if (project.kind === "error") return <ErrorNotice error={project.error} />;

  return (
    <section>
      <p>
        <Link to="/projects">← All projects</Link>
      </p>
      <h1>{project.data.name}</h1>
      {project.data.description && <p>{project.data.description}</p>}
      <p>
        <Link to={`/projects/${projectId}/activity`}>View activity for this project</Link>
      </p>

      <h2>Upload documents</h2>
      <p>PDF, Word (.docx), text (.txt) or Markdown (.md). Up to 10 files at once, 20 MB each.</p>
      <div
        className="dropzone"
        onDragOver={(e: DragEvent) => e.preventDefault()}
        onDrop={(e: DragEvent) => {
          e.preventDefault();
          choose(e.dataTransfer.files);
        }}
      >
        <label htmlFor="files">Choose files or drop them here</label>
        <input
          id="files"
          type="file"
          multiple
          accept={ACCEPT}
          onChange={(e: ChangeEvent<HTMLInputElement>) => choose(e.target.files)}
        />
        {selected.length > 0 && <p>{selected.length} file(s) selected</p>}
      </div>
      <button type="button" onClick={() => void onUpload()} disabled={busy || selected.length === 0}>
        {busy ? "Uploading and reading…" : "Upload"}
      </button>
      {error && <ErrorNotice error={error} />}

      {result && (
        <div aria-live="polite">
          <h3>
            Upload result: {result.extracted} read, {result.rejected} not accepted
          </h3>
          <ul className="results">
            {result.results.map((file, index) => (
              <li key={`${file.filename}-${index}`} className={file.status === "extracted" ? "ok" : "bad"}>
                <strong>{file.filename}</strong>:{" "}
                {file.status === "extracted"
                  ? `read successfully (${file.chunk_count} pieces${file.page_count ? `, ${file.page_count} pages` : ""})`
                  : file.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <h2>Documents</h2>
      {documents.kind === "loading" && <p role="status">Loading documents…</p>}
      {documents.kind === "error" && <ErrorNotice error={documents.error} />}
      {documents.kind === "loaded" &&
        (documents.data.length === 0 ? (
          <p>No documents yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Role</th>
                <th>Size</th>
                <th>Pages</th>
                <th>Pieces</th>
                <th>Warnings</th>
              </tr>
            </thead>
            <tbody>
              {documents.data.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <Link to={`/projects/${projectId}/documents/${doc.id}`}>{doc.original_filename}</Link>
                  </td>
                  <td>{doc.role === "primary_brd" ? "Main BRD" : "Supporting"}</td>
                  <td>{formatSize(doc.size_bytes)}</td>
                  <td>{doc.page_count ?? "–"}</td>
                  <td>{doc.chunk_count}</td>
                  <td>{doc.warnings.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
    </section>
  );
}
