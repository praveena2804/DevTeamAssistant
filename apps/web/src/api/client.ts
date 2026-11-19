/**
 * Typed client for the DevTeam AI platform API.
 *
 * Types come from `schema.d.ts`, generated from the API's OpenAPI document (`npm run gen:api`), so the UI
 * cannot drift from the backend contract without a type error. `npm run check:api` fails on drift.
 *
 * Authentication (D-29 (a)): the browser holds only an HttpOnly session cookie scoped to `/api`, obtained
 * once from the sign-in link. Scripts never see it. Every request is same-origin through the Vite proxy.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];
export type HealthResponse = Schemas["HealthResponse"];
export type VersionResponse = Schemas["VersionResponse"];
export type SessionInfo = Schemas["SessionInfo"];
export type Project = Schemas["ProjectOut"];
export type DocumentSummary = Schemas["DocumentOut"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type ChunkPage = Schemas["ChunkPage"];
export type Chunk = Schemas["ChunkOut"];
export type UploadResponse = Schemas["UploadResponse"];
export type FileResult = Schemas["FileResult"];
export type AuditPage = Schemas["AuditPage"];
export type AuditEvent = Schemas["AuditEventOut"];

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  acceptStatus?: readonly number[];
};

async function errorFrom(response: Response, path: string): Promise<ApiError> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const { message, code } = detail as { message: string; code?: string };
      return new ApiError(response.status, message, code ?? null);
    }
    if (Array.isArray(detail)) {
      return new ApiError(response.status, "Some of the entered values are not valid.", "request.invalid");
    }
  } catch {
    // fall through to the generic message
  }
  return new ApiError(response.status, `${path} returned HTTP ${response.status}`);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method: options.method ?? "GET", headers, credentials: "same-origin" };
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  } else if (options.form) {
    init.body = options.form; // the browser sets the multipart boundary itself
  }
  if (options.signal) init.signal = options.signal;
  const response = await fetch(path, init);
  if (!response.ok && !(options.acceptStatus ?? []).includes(response.status)) {
    throw await errorFrom(response, path);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const opt = (signal?: AbortSignal) => (signal ? { signal } : {});

/** `/health` answers 503 with a valid body when a component is unhealthy, so 503 is not an exception. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { acceptStatus: [503], ...opt(signal) });
}

export function getVersion(signal?: AbortSignal): Promise<VersionResponse> {
  return request<VersionResponse>("/version", opt(signal));
}

// ---------------------------------------------------------------- session
export const signIn = (token: string) => request<SessionInfo>("/api/session", { method: "POST", json: { token } });
export const getSession = (signal?: AbortSignal) => request<SessionInfo>("/api/session", opt(signal));
export const signOut = () => request<void>("/api/session", { method: "DELETE" });

// ---------------------------------------------------------------- projects
export const listProjects = (signal?: AbortSignal) => request<Project[]>("/api/projects", opt(signal));
export const getProject = (projectId: string, signal?: AbortSignal) =>
  request<Project>(`/api/projects/${encodeURIComponent(projectId)}`, opt(signal));
export const createProject = (name: string, description: string) =>
  request<Project>("/api/projects", { method: "POST", json: { name, description } });

// ---------------------------------------------------------------- documents
const docsPath = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/documents`;

export const listDocuments = (projectId: string, signal?: AbortSignal) =>
  request<DocumentSummary[]>(docsPath(projectId), opt(signal));

export function uploadDocuments(projectId: string, files: readonly File[]): Promise<UploadResponse> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return request<UploadResponse>(docsPath(projectId), { method: "POST", form });
}

export const getDocument = (projectId: string, documentId: string, signal?: AbortSignal) =>
  request<DocumentDetail>(`${docsPath(projectId)}/${encodeURIComponent(documentId)}`, opt(signal));

export const listChunks = (projectId: string, documentId: string, offset: number, limit: number, signal?: AbortSignal) =>
  request<ChunkPage>(
    `${docsPath(projectId)}/${encodeURIComponent(documentId)}/chunks?offset=${offset}&limit=${limit}`,
    opt(signal),
  );

// ---------------------------------------------------------------- audit
export const listAudit = (projectId: string | null, offset: number, limit: number, signal?: AbortSignal) =>
  request<AuditPage>(
    projectId
      ? `/api/projects/${encodeURIComponent(projectId)}/audit?offset=${offset}&limit=${limit}`
      : `/api/audit?offset=${offset}&limit=${limit}`,
    opt(signal),
  );
