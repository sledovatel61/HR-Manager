/**
 * Клиент endpoint /health.
 *
 * Запросы идут на тот же origin: в разработке /health проксирует dev-сервер
 * Vite, в Docker — nginx. Прямых обращений к backend из браузера нет,
 * поэтому CORS не требуется.
 */

export type ComponentStatus = "up" | "down";
export type OverallStatus = "ok" | "degraded";

export interface HealthResponse {
  status: OverallStatus;
  database: ComponentStatus;
  version: string;
  checked_at: string;
}

/** Backend жив, но сообщает о недоступности зависимости (HTTP 503). */
export class DegradedHealthError extends Error {
  constructor(public readonly payload: HealthResponse) {
    super(`Приложение работает в деградированном режиме: ${payload.status}`);
    this.name = "DegradedHealthError";
  }
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch("/health", {
    headers: { Accept: "application/json" },
    signal,
  });
  const payload = (await response.json()) as HealthResponse;
  if (!response.ok) {
    throw new DegradedHealthError(payload);
  }
  return payload;
}
