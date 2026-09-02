import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import type { HealthResponse } from "./types";

const HEALTHY: HealthResponse = {
  status: "ok",
  service: "hr-manager",
  version: "0.1.0",
  environment: "development",
  checks: { database: { status: "ok", latency_ms: 3 } },
};

const DATABASE_DOWN: HealthResponse = {
  status: "degraded",
  service: "hr-manager",
  version: "0.1.0",
  environment: "development",
  checks: { database: { status: "error", latency_ms: null } },
};

describe("App health status page", () => {
  it("shows the green state when backend and database are healthy", async () => {
    render(<App healthFetcher={vi.fn().mockResolvedValue(HEALTHY)} />);

    expect(await screen.findByText("Система работает")).toBeInTheDocument();
    expect(screen.getByText(/доступна \(запрос занял 3 мс\)/)).toBeInTheDocument();
    expect(screen.getByText("hr-manager")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
  });

  it("shows the database error state when the database check fails", async () => {
    render(<App healthFetcher={vi.fn().mockResolvedValue(DATABASE_DOWN)} />);

    expect(await screen.findByText("Проблема с базой данных")).toBeInTheDocument();
    expect(screen.getByText("недоступна")).toBeInTheDocument();
  });

  it("shows the offline state when the backend is unreachable", async () => {
    render(<App healthFetcher={vi.fn().mockResolvedValue(null)} />);

    expect(await screen.findByText("Backend недоступен")).toBeInTheDocument();
    expect(screen.getByText("недоступен")).toBeInTheDocument();
  });

  it("refreshes the status when the button is clicked", async () => {
    const fetcher = vi
      .fn<() => Promise<HealthResponse | null>>()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(HEALTHY);
    render(<App healthFetcher={fetcher} />);

    expect(await screen.findByText("Backend недоступен")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Проверить снова" }));

    expect(await screen.findByText("Система работает")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
