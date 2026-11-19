import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const handler = routes[`${method} ${url}`];
    if (!handler) return json({ detail: { code: "test.unmocked", message: `unmocked ${method} ${url}` } }, 404);
    return handler(init);
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const PROJECT = {
  id: "prj_01",
  name: "Library Demo",
  description: "Pilot",
  status: "active",
  created_at: "2026-11-19T09:00:00Z",
  updated_at: "2026-11-19T09:30:00Z",
  document_count: 1,
};

const DOC = {
  id: "doc_01",
  project_id: "prj_01",
  role: "primary_brd",
  original_filename: "brd.pdf",
  media_type: "application/pdf",
  size_bytes: 2048,
  sha256: "a".repeat(64),
  status: "extracted",
  page_count: 3,
  chunk_count: 2,
  char_count: 900,
  warnings: [{ code: "empty_page", message: "Page 2 has no readable text (it may be a scanned image).", page: 2 }],
  extractor: "pdfplumber",
  extractor_version: "1+chunker1",
  created_at: "2026-11-19T09:10:00Z",
};

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("Projects dashboard", () => {
  it("lists projects and creates a new one", async () => {
    const fetchMock = mockApi({
      "GET /api/projects": () => json([PROJECT]),
      "POST /api/projects": () => json({ ...PROJECT, id: "prj_02", name: "New One", document_count: 0 }, 201),
      "GET /api/projects/prj_02": () => json({ ...PROJECT, id: "prj_02", name: "New One", document_count: 0 }),
      "GET /api/projects/prj_02/documents": () => json([]),
    });
    renderAt("/projects");
    expect(await screen.findByRole("link", { name: "Library Demo" })).toHaveAttribute("href", "/projects/prj_01");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New One" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("heading", { name: "New One" })).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: "New One", description: "" });
    expect(post?.[1]?.credentials).toBe("same-origin");
  });

  it("asks the user to sign in when the API answers 401", async () => {
    mockApi({
      "GET /api/projects": () =>
        json({ detail: { code: "auth.required", message: "Sign in first" } }, 401),
    });
    renderAt("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent("You are not signed in");
    expect(screen.getByRole("link", { name: "sign in with your key" })).toHaveAttribute("href", "/signin");
  });

  it("shows the server's plain-language error, e.g. a duplicate name", async () => {
    mockApi({
      "GET /api/projects": () => json([]),
      "POST /api/projects": () =>
        json({ detail: { code: "project.name_taken", message: "A project with this name already exists." } }, 409),
    });
    renderAt("/projects");
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Dup" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A project with this name already exists.");
  });
});

describe("Sign-in", () => {
  it("uses the key from the link fragment once and removes it from the address bar", async () => {
    window.history.replaceState(null, "", "/signin#token=secret-key-123");
    const fetchMock = mockApi({
      "POST /api/session": () => json({ principal_id: "prn_1", display_name: "Local owner", roles: ["project_user"] }),
      "GET /api/projects": () => json([]),
    });
    renderAt("/signin");
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([url]) => String(url) === "/api/session");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toEqual({ token: "secret-key-123" });
    expect(window.location.hash).toBe("");
  });

  it("explains a wrong key", async () => {
    mockApi({
      "POST /api/session": () => json({ detail: { code: "auth.invalid_token", message: "bad" } }, 401),
    });
    renderAt("/signin");
    fireEvent.change(screen.getByLabelText("Sign-in key"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That key is not valid");
  });
});

describe("Project page upload", () => {
  it("uploads files and shows a result per file, including plain-language rejections", async () => {
    const fetchMock = mockApi({
      "GET /api/projects/prj_01": () => json(PROJECT),
      "GET /api/projects/prj_01/documents": () => json([DOC]),
      "POST /api/projects/prj_01/documents": () =>
        json({
          extracted: 1,
          rejected: 1,
          results: [
            { filename: "brd.docx", status: "extracted", document_id: "doc_02", chunk_count: 7, page_count: null, warnings: [] },
            {
              filename: "corrupt.docx",
              status: "rejected",
              code: "upload.corrupt",
              message: "This Word file is damaged or is not a real .docx document.",
              chunk_count: 0,
              warnings: [],
            },
          ],
        }),
    });
    renderAt("/projects/prj_01");
    expect(await screen.findByRole("link", { name: "brd.pdf" })).toHaveAttribute(
      "href",
      "/projects/prj_01/documents/doc_01",
    );
    const files = [new File(["a"], "brd.docx"), new File(["b"], "corrupt.docx")];
    fireEvent.change(screen.getByLabelText("Choose files or drop them here"), { target: { files } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText(/1 read, 1 not accepted/)).toBeInTheDocument();
    expect(screen.getByText(/read successfully \(7 pieces\)/)).toBeInTheDocument();
    expect(screen.getByText("corrupt.docx").closest("li")).toHaveTextContent(
      "corrupt.docx: This Word file is damaged or is not a real .docx document.",
    );
    const upload = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    const body = upload?.[1]?.body as FormData;
    expect(body.getAll("files")).toHaveLength(2);
  });
});

describe("Extraction review", () => {
  it("shows warnings, the outline and each piece of text with its source reference", async () => {
    const chunk = (n: number) => ({
      id: `chk_${n}`,
      ordinal: n,
      kind: n === 1 ? "table" : "text",
      text: n === 1 ? "Role | Can do" : "FR-001 The librarian shall add books.",
      section_path: "3 Functional Requirements",
      page_start: 2,
      page_end: 2,
      paragraph_start: n + 1,
      paragraph_end: n + 1,
      reference: `brd.pdf · p.2 · § 3 Functional Requirements · ¶${n + 1}`,
    });
    const fetchMock = mockApi({
      "GET /api/projects/prj_01/documents/doc_01": () =>
        json({ ...DOC, outline: [{ level: 1, title: "3 Functional Requirements", page: 2 }] }),
      "GET /api/projects/prj_01/documents/doc_01/chunks?offset=0&limit=20": () =>
        json({ items: [chunk(0), chunk(1)], total: 25, offset: 0, limit: 20 }),
      "GET /api/projects/prj_01/documents/doc_01/chunks?offset=20&limit=20": () =>
        json({ items: [chunk(20)], total: 25, offset: 20, limit: 20 }),
    });
    renderAt("/projects/prj_01/documents/doc_01");
    expect(await screen.findByText("brd.pdf · p.2 · § 3 Functional Requirements · ¶1")).toBeInTheDocument();
    expect(screen.getByText(/Page 2 has no readable text/)).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Document outline" })).toHaveTextContent("3 Functional Requirements");
    expect(screen.getByText("table")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–2 of 25")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("brd.pdf · p.2 · § 3 Functional Requirements · ¶21")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain(
        "/api/projects/prj_01/documents/doc_01/chunks?offset=20&limit=20",
      ),
    );
  });
});

describe("Activity", () => {
  it("lists audit events with plain outcome labels", async () => {
    mockApi({
      "GET /api/audit?offset=0&limit=50": () =>
        json({
          items: [
            {
              id: "aud_2",
              ts: "2026-11-19T09:31:00Z",
              principal_id: null,
              action: "auth.denied",
              project_id: null,
              target_type: null,
              target_id: null,
              outcome: "denied",
              request_id: "req_2",
              details: { reason: "missing_or_invalid_credentials" },
            },
            {
              id: "aud_1",
              ts: "2026-11-19T09:30:00Z",
              principal_id: "prn_1",
              action: "document.upload",
              project_id: "prj_01",
              target_type: "document",
              target_id: "doc_01",
              outcome: "success",
              request_id: "req_1",
              details: { filename: "brd.pdf" },
            },
          ],
          total: 2,
          offset: 0,
          limit: 50,
        }),
    });
    renderAt("/activity");
    expect(await screen.findByText("auth.denied")).toBeInTheDocument();
    expect(screen.getByText("Refused")).toBeInTheDocument();
    expect(screen.getByText("missing or invalid credentials")).toBeInTheDocument();
    expect(screen.getByText("brd.pdf")).toBeInTheDocument();
  });
});
