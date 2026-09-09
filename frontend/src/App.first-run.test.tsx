import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => {
  window.history.pushState({}, "", "/");
});

describe("App routing: /first-run is a standalone pilot page (phase 12)", () => {
  it("renders the first-run flow and skips the auth gate entirely", async () => {
    window.history.pushState({}, "", "/first-run");
    const currentUserFetcher = vi.fn();
    render(<App currentUserFetcher={currentUserFetcher} />);

    expect(await screen.findByText("HR Manager — первый запуск")).toBeInTheDocument();
    // The page must not poke the authenticated endpoints on mount.
    expect(currentUserFetcher).not.toHaveBeenCalled();
  });

  it("keeps the normal login gate on other paths", async () => {
    render(<App currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))} />);
    expect(await screen.findByRole("heading", { name: "Вход в HR Manager" })).toBeInTheDocument();
  });
});
