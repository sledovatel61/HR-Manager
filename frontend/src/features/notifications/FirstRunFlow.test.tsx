import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FirstRunFlow } from "./FirstRunFlow";
import { tokenFromHash } from "./firstRunToken";
import type { CurrentUser, User } from "../../types";

const OWNER: CurrentUser = {
  user: {
    id: "11111111-1111-1111-1111-111111111111",
    username: "pilot-ivanov",
    full_name: "Иванов",
    role: "admin",
    working_mode: "hr",
    password_change_required: true,
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-09-09T10:00:00Z",
  },
  csrf_token: "test-csrf-token",
};

const OWNER_READY: User = { ...OWNER.user, password_change_required: false };

const HASH = "#first-run?t=abcDEF1234567890_-";

describe("tokenFromHash", () => {
  it("extracts the exchange token from the location hash", () => {
    expect(tokenFromHash("#first-run?t=abcDEF1234567890_-")).toBe("abcDEF1234567890_-");
    expect(tokenFromHash("?t=abc")).toBe("abc");
    expect(tokenFromHash("#first-run")).toBeNull();
  });
});

describe("FirstRunFlow", () => {
  it("claims the token and shows the owner surname and working mode", async () => {
    const claimFetcher = vi.fn().mockResolvedValue(OWNER);
    render(
      <FirstRunFlow
        status={{ pending: true, needs_password: false }}
        onDone={vi.fn()}
        claimFetcher={claimFetcher}
        hash={HASH}
      />
    );

    expect(
      await screen.findByRole("heading", { name: "Добро пожаловать, Иванов!" })
    ).toBeInTheDocument();
    expect(claimFetcher).toHaveBeenCalledWith("abcDEF1234567890_-");
    // Working mode and technical login are shown; the password is not.
    expect(screen.getByText("HR")).toBeInTheDocument();
    expect(screen.getByText("pilot-ivanov")).toBeInTheDocument();
  });

  it("saves the chosen password and finishes with the updated user", async () => {
    const onDone = vi.fn();
    const passwordFetcher = vi.fn().mockResolvedValue(OWNER_READY);
    render(
      <FirstRunFlow
        status={{ pending: true, needs_password: false }}
        onDone={onDone}
        claimFetcher={vi.fn().mockResolvedValue(OWNER)}
        passwordFetcher={passwordFetcher}
        hash={HASH}
      />
    );

    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Добро пожаловать, Иванов!" });
    await user.type(screen.getByLabelText(/Пароль$/), "Str0ng-Pass-2026");
    await user.type(screen.getByLabelText(/Повторите пароль/), "Str0ng-Pass-2026");
    await user.click(screen.getByRole("button", { name: "Сохранить и продолжить" }));

    expect(passwordFetcher).toHaveBeenCalledWith("Str0ng-Pass-2026");
    // onDone is called with the refreshed user (password_change_required cleared).
    await vi.waitFor(() => expect(onDone).toHaveBeenCalledWith({ ...OWNER, user: OWNER_READY }));
  });

  it("rejects a too-short password before calling the API", async () => {
    const passwordFetcher = vi.fn();
    render(
      <FirstRunFlow
        status={{ pending: true, needs_password: false }}
        onDone={vi.fn()}
        claimFetcher={vi.fn().mockResolvedValue(OWNER)}
        passwordFetcher={passwordFetcher}
        hash={HASH}
      />
    );

    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Добро пожаловать, Иванов!" });
    await user.type(screen.getByLabelText(/Пароль$/), "short");
    await user.type(screen.getByLabelText(/Повторите пароль/), "short");
    await user.click(screen.getByRole("button", { name: "Сохранить и продолжить" }));

    expect(
      await screen.findByText("Пароль должен содержать не менее 12 символов.")
    ).toBeInTheDocument();
    expect(passwordFetcher).not.toHaveBeenCalled();
  });

  it("rejects mismatched confirmation before calling the API", async () => {
    const passwordFetcher = vi.fn();
    render(
      <FirstRunFlow
        status={{ pending: true, needs_password: false }}
        onDone={vi.fn()}
        claimFetcher={vi.fn().mockResolvedValue(OWNER)}
        passwordFetcher={passwordFetcher}
        hash={HASH}
      />
    );

    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Добро пожаловать, Иванов!" });
    await user.type(screen.getByLabelText(/Пароль$/), "Str0ng-Pass-2026");
    await user.type(screen.getByLabelText(/Повторите пароль/), "Str0ng-Pass-9999");
    await user.click(screen.getByRole("button", { name: "Сохранить и продолжить" }));

    expect(await screen.findByText("Пароли не совпадают.")).toBeInTheDocument();
    expect(passwordFetcher).not.toHaveBeenCalled();
  });

  it("shows the error step when the claim fails", async () => {
    render(
      <FirstRunFlow
        status={{ pending: true, needs_password: false }}
        onDone={vi.fn()}
        claimFetcher={vi.fn().mockRejectedValue(new Error("Код первого запуска истёк."))}
        hash={HASH}
      />
    );

    expect(
      await screen.findByRole("heading", { name: "Не удалось завершить первый запуск" })
    ).toBeInTheDocument();
    expect(screen.getByText("Код первого запуска истёк.")).toBeInTheDocument();
  });

  it("shows the waiting step when the URL carries no token", async () => {
    render(
      <FirstRunFlow
        status={{ pending: true, needs_password: false }}
        onDone={vi.fn()}
        claimFetcher={vi.fn()}
        hash="#first-run"
      />
    );

    expect(
      await screen.findByRole("heading", { name: "Первый запуск HR Manager" })
    ).toBeInTheDocument();
    expect(screen.getByTestId("first-run-waiting")).toBeInTheDocument();
  });

  it("hints at the password step when the owner still needs a password", async () => {
    render(
      <FirstRunFlow
        status={{ pending: false, needs_password: true }}
        onDone={vi.fn()}
        claimFetcher={vi.fn()}
        hash="#first-run"
      />
    );

    expect(await screen.findByTestId("first-run-waiting")).toBeInTheDocument();
    expect(screen.getByText(/Осталось задать пароль владельца/)).toBeInTheDocument();
  });
});
