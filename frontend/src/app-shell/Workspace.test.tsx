import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import type { CurrentUser } from "../types";

const PILOT_HR: CurrentUser = {
  user: {
    id: "22222222-2222-2222-2222-222222222222",
    username: "pilot-ivanov",
    full_name: "Иванов",
    role: "admin",
    working_mode: "hr",
    password_change_required: false,
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-09-09T10:00:00Z",
  },
  csrf_token: "test-csrf-token",
};

describe("Workspace working mode", () => {
  it("shows the pilot owner's chosen working mode next to the role", async () => {
    render(<App currentUserFetcher={vi.fn().mockResolvedValue(PILOT_HR)} />);

    // The topbar shows the server role (Администратор) and the chosen
    // working mode (HR) — the owner keeps full access either way.
    expect(await screen.findByText("Администратор · HR")).toBeInTheDocument();
  });

  it("starts an HR-mode pilot on the shared candidates screen", async () => {
    render(<App currentUserFetcher={vi.fn().mockResolvedValue(PILOT_HR)} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Кандидаты" })
    ).toBeInTheDocument();
  });
});
