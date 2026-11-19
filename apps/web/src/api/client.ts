/**
 * Typed client for the DevTeam AI platform API.
 *
 * Types come from `schema.d.ts`, generated from the API's OpenAPI document (`npm run gen:api`), so the UI
 * cannot drift from the backend contract without a type error. `npm run check:api` fails on drift.
 */
import type { components } from "./schema";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type VersionResponse = components["schemas"]["VersionResponse"];

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type RequestOptions = { signal?: AbortSignal; acceptStatus?: readonly number[] };

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const init: RequestInit = { headers: { Accept: "application/json" } };
  if (options.signal) init.signal = options.signal;
  const response = await fetch(path, init);
  if (!response.ok && !(options.acceptStatus ?? []).includes(response.status)) {
    throw new ApiError(response.status, `${path} returned HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

/** `/health` answers 503 with a valid body when a component is unhealthy, so 503 is not an exception. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/health", { acceptStatus: [503], ...(signal ? { signal } : {}) });
}

export function getVersion(signal?: AbortSignal): Promise<VersionResponse> {
  return requestJson<VersionResponse>("/version", signal ? { signal } : {});
}
