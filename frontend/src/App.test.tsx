import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import App from "./App";
import { classifyHealth, fetchHealth, type HealthPayload } from "./api/health";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function payload(overrides: Partial<HealthPayload> = {}): HealthPayload {
  return {
    status: "ok",
    app: "HR Manager API",
    version: "0.1.0",
    environment: "test",
    database: "ok",
    ...overrides,
  };
}

function stubFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const init = status === 200 ? { status, headers: { "Content-Type": "application/json" } } : { status };
      return new Response(JSON.stringify(body), init);
    }),
  );
}

describe("classifyHealth", () => {
  it("healthy ответ превращается в ok/ok", () => {
    expect(classifyHealth(payload())).toEqual({ backend: "ok", database: "ok" });
  });

  it("ответ с недоступной БД помечает backend как degraded", () => {
    expect(classifyHealth(payload({ status: "error", database: "unavailable" }))).toEqual({
      backend: "degraded",
      database: "unavailable",
    });
  });
});

describe("fetchHealth", () => {
  it("возвращает payload при HTTP 200", async () => {
    stubFetch(200, payload());
    await expect(fetchHealth()).resolves.toEqual(payload());
  });

  it("парсит ответ при HTTP 503 (backend жив, БД нет)", async () => {
    stubFetch(503, payload({ status: "error", database: "unavailable" }));
    await expect(fetchHealth()).resolves.toMatchObject({
      status: "error",
      database: "unavailable",
    });
  });
});

describe("App (страница состояния)", () => {
  it("показывает healthy-состояние backend и БД после успешного /health", async () => {
    stubFetch(200, payload());
    render(<App />);

    // Первичное состояние — проверка выполняется.
    expect(await screen.findByText("Проверка…")).toBeTruthy();

    // После ответа появляются зелёные статусы.
    expect(await screen.findByText("Работает")).toBeTruthy();
    expect(await screen.findByText("Подключена")).toBeTruthy();
    expect(await screen.findByText(/версия 0\.1\.0/)).toBeTruthy();
  });

  it("показывает degraded-состояние при HTTP 503 от backend", async () => {
    stubFetch(503, payload({ status: "error", database: "unavailable" }));
    render(<App />);

    expect(await screen.findByText("Неисправен (БД недоступна)")).toBeTruthy();
    expect(await screen.findByText("Недоступна")).toBeTruthy();
  });

  it("показывает unreachable-состояние при сетевой ошибке", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    render(<App />);

    expect(await screen.findByText("Недоступен")).toBeTruthy();
    expect(await screen.findByText(/Failed to fetch/)).toBeTruthy();
  });
});
