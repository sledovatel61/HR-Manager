import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import FirstRun from "./FirstRun";
import type { FirstRunClaimResult, FirstRunState, OpsReadinessStatus } from "../types";

const STATE_FRESH: FirstRunState = {
  pending: true,
  pending_work_role: "hr",
  pending_expires_in_seconds: 900,
  fresh_install: true,
  pilot_owner_exists: false,
};

const CLAIM: FirstRunClaimResult = {
  user: {
    id: "9e2a0f3b-5b39-4f70-9a2c-a897e2e77777",
    username: "ivanov-pilot-a1f3",
    full_name: "Иванов",
    role: "admin",
    work_role: "hr",
    password_is_bootstrap: true,
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-09-09T10:00:00Z",
  },
  csrf_token: "tok",
  must_set_password: true,
};

const READY: OpsReadinessStatus = {
  status: "ok",
  release_sha: "aaaa",
  database: { status: "ok" },
  migrations: { ok: true },
  notifications: { worker_alive: true },
  backup: { available: true, ok: true, age_seconds: 60 },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FirstRun flow (pilot phase 12)", () => {
  it("routes to the login page when the pilot was already claimed", async () => {
    const finished = vi.fn();
    render(
      <FirstRun
        stateFetcher={vi.fn().mockResolvedValue({ ...STATE_FRESH, pilot_owner_exists: true })}
        onFinished={finished}
      />
    );
    expect(await screen.findByText("Пилот уже настроен")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ко входу" }));
    expect(finished).toHaveBeenCalled();
  });

  it("rejects a malformed code on the client without a request", async () => {
    const claim = vi.fn();
    render(
      <FirstRun
        stateFetcher={vi.fn().mockResolvedValue(STATE_FRESH)}
        claimFetcher={claim}
      />
    );
    await userEvent.type(await screen.findByLabelText(/Код подтверждения/), "ab!!");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить код" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("6–8 символов");
    expect(claim).not.toHaveBeenCalled();
  });

  it("shows the server error for a wrong/expired code", async () => {
    render(
      <FirstRun
        stateFetcher={vi.fn().mockResolvedValue(STATE_FRESH)}
        claimFetcher={vi.fn().mockRejectedValue(new Error("Код не найден или уже использован."))}
      />
    );
    await userEvent.type(await screen.findByLabelText(/Код подтверждения/), "K7M2QX");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить код" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже использован");
  });

  it("completes claim → own password → readiness confirmation", async () => {
    const finished = vi.fn();
    const password = vi.fn().mockResolvedValue(undefined);
    render(
      <FirstRun
        stateFetcher={vi.fn().mockResolvedValue(STATE_FRESH)}
        claimFetcher={vi.fn().mockResolvedValue(CLAIM)}
        passwordFetcher={password}
        statusFetcher={vi.fn().mockResolvedValue(READY)}
        onFinished={finished}
      />
    );
    await userEvent.type(await screen.findByLabelText(/Код подтверждения/), "k7m2qx");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить код" }));

    // The username shown is the deterministic server-side one; the generated
    // bootstrap password is never displayed or asked for.
    expect(await screen.findByText(/ivanov-pilot-a1f3/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/Новый пароль/), "Str0ng-Pilot-2026");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "Str0ng-Pilot-2026");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить пароль" }));

    await waitFor(() => expect(password).toHaveBeenCalledWith("Str0ng-Pilot-2026"));
    const finishButton = await screen.findByRole("button", { name: "Открыть HR Manager" });
    expect(screen.getAllByText(/✅/).length).toBeGreaterThanOrEqual(5);
    await userEvent.click(finishButton);
    expect(finished).toHaveBeenCalled();
  });

  it("enforces the password policy before calling the API", async () => {
    const password = vi.fn();
    render(
      <FirstRun
        stateFetcher={vi.fn().mockResolvedValue(STATE_FRESH)}
        claimFetcher={vi.fn().mockResolvedValue(CLAIM)}
        passwordFetcher={password}
        statusFetcher={vi.fn().mockResolvedValue(READY)}
      />
    );
    await userEvent.type(await screen.findByLabelText(/Код подтверждения/), "K7M2QX");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить код" }));
    await screen.findByLabelText(/Новый пароль/);
    await userEvent.type(screen.getByLabelText(/Новый пароль/), "short1");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "short1");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить пароль" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("от 12 до 128 символов");
    expect(password).not.toHaveBeenCalled();
  });

  it("waits for readiness: button stays disabled while services settle", async () => {
    const notYet: OpsReadinessStatus = { ...READY, status: "degraded" };
    render(
      <FirstRun
        stateFetcher={vi.fn().mockResolvedValue(STATE_FRESH)}
        claimFetcher={vi.fn().mockResolvedValue(CLAIM)}
        passwordFetcher={vi.fn().mockResolvedValue(undefined)}
        statusFetcher={vi.fn().mockResolvedValue(notYet)}
      />
    );
    await userEvent.type(await screen.findByLabelText(/Код подтверждения/), "K7M2QX");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить код" }));
    await screen.findByLabelText(/Новый пароль/);
    await userEvent.type(screen.getByLabelText(/Новый пароль/), "Str0ng-Pilot-2026");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "Str0ng-Pilot-2026");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить пароль" }));
    const button = await screen.findByRole("button", { name: "Ждём готовности…" });
    expect(button).toBeDisabled();
  });
});
