import type { HealthResponse } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

/** Fetch the backend health report, or null when the backend is unreachable. */
export async function fetchHealth(): Promise<HealthResponse | null> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/health`, { headers: { Accept: "application/json" } });
  } catch {
    // Network-level failure: the backend is unreachable. UI shows "offline".
    return null;
  }
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as HealthResponse;
}
