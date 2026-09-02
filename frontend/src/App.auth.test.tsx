import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import type { CurrentUser } from "./types";

const ADMIN: CurrentUser = {
  user: {
    id: "11111111-1111-1111-1111-111111111111",
    username: "admin1",
    full_name: "Админ Админов",
    role: "admin",
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-09-02T10:00:00Z",
  },
  csrf_token: "test-csrf-token",
};

describe("App authentication flow", () => {
  it("shows the login form when there is no active session", async () => {
    render(<App currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))} />);

    expect(
      await screen.findByRole("heading", { name: "Вход в HR Manager" })
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Имя пользователя/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Пароль/)).toBeInTheDocument();
  });

  it("logs in with credentials and shows the current user", async () => {
    const currentUserFetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("401"))
      .mockResolvedValue(ADMIN);
    const loginFetcher = vi.fn().mockResolvedValue(ADMIN);

    render(<App currentUserFetcher={currentUserFetcher} loginFetcher={loginFetcher} />);

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Имя пользователя/), "admin1");
    await user.type(screen.getByLabelText(/Пароль/), "Str0ng-Pass-2026");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(loginFetcher).toHaveBeenCalledWith("admin1", "Str0ng-Pass-2026");
    expect(await screen.findByText("Вы вошли")).toBeInTheDocument();
    expect(screen.getByText("admin1")).toBeInTheDocument();
    expect(screen.getByText("Администратор")).toBeInTheDocument();
  });

  it("shows an error message when login fails", async () => {
    const loginFetcher = vi
      .fn()
      .mockRejectedValue(new Error("Неверное имя пользователя или пароль."));

    render(
      <App
        currentUserFetcher={vi.fn().mockRejectedValue(new Error("401"))}
        loginFetcher={loginFetcher}
      />
    );

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Имя пользователя/), "ghost");
    await user.type(screen.getByLabelText(/Пароль/), "Wrong-Password-1");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(
      await screen.findByText("Неверное имя пользователя или пароль.")
    ).toBeInTheDocument();
  });

  it("restores an existing session and shows the user", async () => {
    render(<App currentUserFetcher={vi.fn().mockResolvedValue(ADMIN)} />);

    expect(await screen.findByText("Вы вошли")).toBeInTheDocument();
    expect(screen.getByText("admin1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
  });

  it("shows a loading state while checking the session", () => {
    render(<App currentUserFetcher={() => new Promise<CurrentUser>(() => undefined)} />);
    expect(screen.getByText("Проверяем сессию…")).toBeInTheDocument();
  });
});
