import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App first-run gating", () => {
  it("shows the first-run flow instead of the login form while the pilot is pending", async () => {
    render(
      <App
        currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))}
        firstRunStatusFetcher={vi.fn().mockResolvedValue({ pending: true, needs_password: false })}
      />
    );

    expect(await screen.findByTestId("first-run-waiting")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Вход в HR Manager" })).not.toBeInTheDocument();
  });

  it("forces the password step when the owner has not set one yet", async () => {
    render(
      <App
        currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))}
        firstRunStatusFetcher={vi.fn().mockResolvedValue({ pending: false, needs_password: true })}
      />
    );

    expect(await screen.findByTestId("first-run-waiting")).toBeInTheDocument();
    expect(screen.getByText(/Осталось задать пароль владельца/)).toBeInTheDocument();
  });

  it("falls back to the login form once the pilot is fully set up", async () => {
    render(
      <App
        currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))}
        firstRunStatusFetcher={vi.fn().mockResolvedValue({ pending: false, needs_password: false })}
      />
    );

    expect(
      await screen.findByRole("heading", { name: "Вход в HR Manager" })
    ).toBeInTheDocument();
  });

  it("ignores a failed first-run status probe and still shows the login form", async () => {
    render(
      <App
        currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))}
        firstRunStatusFetcher={vi.fn().mockRejectedValue(new Error("network"))}
      />
    );

    expect(
      await screen.findByRole("heading", { name: "Вход в HR Manager" })
    ).toBeInTheDocument();
  });
});
