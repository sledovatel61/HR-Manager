/**
 * Клиент для GET /health бэкенда.
 *
 * Запрос идёт по относительному пути "/health": в dev-режиме Vite проксирует
 * его на backend (см. vite.config.ts), в production тот же путь обслуживает
 * reverse-proxy. Прямых обращений к localhost из браузера нет.
 */

export interface HealthPayload {
  status: "ok" | "error";
  app: string;
  version: string;
  environment: string;
  database: "ok" | "unavailable";
}

/** Состояние backend-сервиса с точки зрения страницы. */
export type BackendState = "checking" | "ok" | "degraded" | "unreachable";

/** Состояние базы данных. */
export type DatabaseState = "unknown" | "ok" | "unavailable";

export interface ServiceHealth {
  backend: BackendState;
  database: DatabaseState;
}

export class HealthError extends Error {}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthPayload> {
  const response = await fetch("/health", {
    signal,
    headers: { Accept: "application/json" },
  });

  if (response.status === 503) {
    // Backend ответил, но база недоступна — пробуем распарсить детали.
    const payload = (await response.json().catch(() => null)) as Partial<HealthPayload> | null;
    return {
      status: "error",
      app: payload?.app ?? "HR Manager API",
      version: payload?.version ?? "unknown",
      environment: payload?.environment ?? "unknown",
      database: payload?.database ?? "unavailable",
    };
  }

  if (!response.ok) {
    throw new HealthError(`Backend /health вернул HTTP ${response.status}`);
  }

  return (await response.json()) as HealthPayload;
}

/** Преобразует ответ backend в состояние, понятное интерфейсу. */
export function classifyHealth(payload: HealthPayload): ServiceHealth {
  if (payload.status === "ok") {
    return { backend: "ok", database: payload.database };
  }
  return { backend: "degraded", database: payload.database };
}
