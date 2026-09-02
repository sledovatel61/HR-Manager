import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App shell", () => {
  it("renders the application title", async () => {
    render(<App currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))} />);
    // Wait for the session check to settle (login form appears).
    expect(
      await screen.findByRole("heading", { name: "Вход в HR Manager" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "HR Manager" })
    ).toBeInTheDocument();
  });
});
