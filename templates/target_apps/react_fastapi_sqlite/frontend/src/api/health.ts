// Starter-kit API client for the backend health endpoint. Generated API clients are added next to this
// file from the frozen OpenAPI contract (Parts K/L).

export type HealthStatus = "ok" | "degraded" | "unreachable";

export interface HealthReply {
  status: string;
}

export function statusFrom(reply: HealthReply | null): HealthStatus {
  if (reply === null) {
    return "unreachable";
  }
  return reply.status === "ok" ? "ok" : "degraded";
}

export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<HealthStatus> {
  try {
    const response = await fetcher("/health");
    if (!response.ok) {
      return "degraded";
    }
    return statusFrom((await response.json()) as HealthReply);
  } catch {
    return "unreachable";
  }
}
