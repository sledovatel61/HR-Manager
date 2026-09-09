import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import FirstRunSetup from "./FirstRunSetup";
import type { CurrentUser, SetupPreview } from "../types";

const PREVIEW: SetupPreview = {
  surname: "Смирнова",
  working_mode: "manager",
  timezone: "Europe/Moscow",
  readiness: { database: "ok", worker: "ok", backup: "ok" },
  channels: { smtp: "not_configured", telegram: "not_configured" },
  full_access: true,
};

const CURRENT: CurrentUser = {
  user: {
    id: "22222222-2222-2222-2222-222222222222",
    username: "owner.smirnova",
    full_name: "Смирнова",
    role: "admin",
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-09-09T10:00:00Z",
  },
  csrf_token: "csrf-after-redeem",
  working_mode: "manager",
};

const TICKET = "ticket-1234567890123456789012345678901234567890123";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** Mock fetch routing on URL: /setup/owner/preview vs /timezones. */
function stubSetupApi(options: { preview?: Response; timezones?: Response }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/setup/owner/preview")) {
      return options.preview ?? Response.json(PREVIEW, { status: 200 });
    }
    if (url.includes("/timezones")) {
      return options.timezones ?? Response.json({ timezones: ["Europe/Moscow"] }, { status: 200 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("FirstRunSetup", () => {
  it("shows the surname and working mode from the ticket preview", async () => {
    stubSetupApi({});

    render(<FirstRunSetup ticket={TICKET} onComplete={vi.fn()} />);

    expect(await screen.findByText("Смирнова")).toBeInTheDocument();
    expect(screen.getByText(/Руководитель/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Пароль владельца/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Завершить настройку" })).toBeInTheDocument();
  });

  it("rejects passwords shorter than 12 characters client-side", async () => {
    stubSetupApi({});
    const onComplete = vi.fn();
    render(<FirstRunSetup ticket={TICKET} onComplete={onComplete} />);

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Пароль владельца/), "short");
    await user.type(screen.getByLabelText(/Повторите пароль/), "short");
    await user.click(screen.getByRole("button", { name: "Завершить настройку" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/не короче 12/);
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("redeems the ticket and clears it from the URL fragment", async () => {
    stubSetupApi({});
    const onComplete = vi.fn();
    window.history.replaceState(null, "", `/#setup=${TICKET}`);

    const redeemFetcher = vi.fn().mockResolvedValue(CURRENT);
    render(<FirstRunSetup ticket={TICKET} onComplete={onComplete} redeemFetcher={redeemFetcher} />);

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Пароль владельца/), "Str0ng-Pass-2026");
    await user.type(screen.getByLabelText(/Повторите пароль/), "Str0ng-Pass-2026");
    await user.click(screen.getByRole("button", { name: "Завершить настройку" }));

    expect(redeemFetcher).toHaveBeenCalledWith(
      expect.objectContaining({
        ticket: TICKET,
        password: "Str0ng-Pass-2026",
        timezone: "Europe/Moscow",
        workdays: [1, 2, 3, 4, 5],
      }),
    );
    expect(onComplete).toHaveBeenCalledWith(CURRENT);
    // The one-time ticket must not linger in the address bar.
    expect(window.location.hash).toBe("");
  });

  it("shows a blocking message when the ticket is already consumed", async () => {
    stubSetupApi({
      preview: Response.json({ detail: "Первоначальная настройка уже завершена." }, { status: 409 }),
    });

    render(<FirstRunSetup ticket={TICKET} onComplete={vi.fn()} />);

    expect(
      await screen.findByText(/Первоначальная настройка уже завершена/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Завершить настройку" })).not.toBeInTheDocument();
  });
});
