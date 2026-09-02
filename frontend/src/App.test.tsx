import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const HEALTH_OK = {
  status: "ok",
  database: "up",
  version: "0.1.0",
  checked_at: "2026-09-02T10:00:00Z",
};

const HEALTH_DEGRADED = {
  ...HEALTH_OK,
  status: "degraded",
  database: "down",
};

function jsonResponse(payload: unknown, status: number): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App — страница состояния каркаса", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("показывает «работает», когда backend и БД доступны", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(HEALTH_OK, 200));
    render(<App />);

    await waitFor(() => expect(screen.getAllByText("Работает")).toHaveLength(1));
    expect(screen.getByText("Доступна")).toBeInTheDocument();
    expect(screen.getByText(/Версия 0\.1\.0/)).toBeInTheDocument();
  });

  it("показывает деградацию, когда backend отвечает 503 из-за БД", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(HEALTH_DEGRADED, 503));
    render(<App />);

    await waitFor(() => expect(screen.getByText("Недоступна")).toBeInTheDocument());
    expect(screen.getByText("Работает")).toBeInTheDocument();
    expect(screen.getByText(/HTTP 503/)).toBeInTheDocument();
  });

  it("показывает недоступность, когда backend не отвечает вовсе", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("fetch failed"));
    render(<App />);

    await waitFor(() => expect(screen.getByText("Недоступен")).toBeInTheDocument());
    expect(screen.getByText("Неизвестно")).toBeInTheDocument();
  });
});
