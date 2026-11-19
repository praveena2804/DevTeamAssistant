import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { createProject, listProjects } from "../api/client";
import { useLoad } from "../api/useLoad";
import { ErrorNotice } from "../components/ErrorNotice";

export function ProjectsPage() {
  const navigate = useNavigate();
  const [state] = useLoad((signal) => listProjects(signal), []);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const project = await createProject(name, description);
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Could not create the project."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Projects</h1>
      {state.kind === "loading" && <p role="status">Loading projects…</p>}
      {state.kind === "error" && <ErrorNotice error={state.error} />}
      {state.kind === "loaded" &&
        (state.data.length === 0 ? (
          <p>No projects yet. Create the first one below.</p>
        ) : (
          <table>
            <caption>Your projects</caption>
            <thead>
              <tr>
                <th>Name</th>
                <th>Documents</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {state.data.map((project) => (
                <tr key={project.id}>
                  <td>
                    <Link to={`/projects/${project.id}`}>{project.name}</Link>
                  </td>
                  <td>{project.document_count}</td>
                  <td>{new Date(project.updated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}

      <h2>Create a project</h2>
      {error && <ErrorNotice error={error} />}
      <form onSubmit={onCreate} className="stack">
        <label htmlFor="project-name">Name</label>
        <input
          id="project-name"
          required
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label htmlFor="project-description">Description (optional)</label>
        <textarea
          id="project-description"
          maxLength={2000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button type="submit" disabled={busy || !name.trim()}>
          Create project
        </button>
      </form>
    </section>
  );
}
