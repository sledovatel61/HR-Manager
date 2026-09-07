import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { ToastProvider } from "../../design-system/components/Toast";
import type { NotificationPreferences } from "../../types";
import { PreferencesPage } from "./PreferencesPage";

vi.mock("../../api");

const MOCK_PREFS_UNLINKED: NotificationPreferences = {
  timezone: "Europe/Moscow",
  quiet_hours_start: "21:00",
  quiet_hours_end: "08:00",
  workdays: [1, 2, 3, 4, 5],
  enabled_types: ["event_assigned", "reminder_due"],
  enabled_channels: ["in_app"],
  initialized: true,
  telegram_chat_id: null,
  telegram_username: null,
  telegram_opt_in: false,
  email_address: null,
  email_opt_in: false,
};

const MOCK_PREFS_LINKED: NotificationPreferences = {
  ...MOCK_PREFS_UNLINKED,
  telegram_chat_id: 987654321,
  telegram_username: "hr_user_tg",
  telegram_opt_in: true,
  telegram_consent_at: "2026-09-07T12:00:00Z",
  email_address: "hr@company.test",
  email_opt_in: true,
  email_consent_at: "2026-09-07T12:00:00Z",
  enabled_channels: ["in_app", "telegram", "email"],
};

describe("PreferencesPage (Phase 9)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listTimezones).mockResolvedValue({
      timezones: ["Europe/Moscow", "UTC", "Asia/Yekaterinburg"],
    });
  });

  it("renders preferences and unlinked Telegram status", async () => {
    vi.mocked(api.getPreferences).mockResolvedValue(MOCK_PREFS_UNLINKED);

    render(
      <ToastProvider>
        <PreferencesPage />
      </ToastProvider>
    );

    expect(await screen.findByText("Время и тихие часы")).toBeInTheDocument();
    expect(screen.getByText("Каналы доставки и согласие")).toBeInTheDocument();
    expect(screen.getByTestId("link-telegram-btn")).toBeInTheDocument();
  });

  it("initiates Telegram linking flow and confirms linking", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getPreferences).mockResolvedValue(MOCK_PREFS_UNLINKED);
    vi.mocked(api.initiateTelegramLink).mockResolvedValue({
      token: "secret-token-123",
      bot_username: "HrManagerBot",
      deep_link: "https://t.me/HrManagerBot?start=secret-token-123",
      expires_at: "2026-09-07T12:30:00Z",
    });
    vi.mocked(api.confirmTelegramLink).mockResolvedValue({
      status: "working",
      configured_in_system: true,
      linked: true,
      opt_in: true,
      has_consent: true,
    });

    render(
      <ToastProvider>
        <PreferencesPage />
      </ToastProvider>
    );

    const linkBtn = await screen.findByTestId("link-telegram-btn");
    await user.click(linkBtn);

    expect(api.initiateTelegramLink).toHaveBeenCalled();
    expect(await screen.findByText("Привязка Telegram")).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/t\.me\/HrManagerBot\?start=secret-token-123/)).toBeInTheDocument();

    const input = screen.getByPlaceholderText("Например: 123456789");
    await user.type(input, "123456789");

    const confirmBtn = screen.getByText("Подтвердить привязку");
    await user.click(confirmBtn);

    expect(api.confirmTelegramLink).toHaveBeenCalledWith({
      token: "secret-token-123",
      chat_id: 123456789,
    });
  });

  it("renders linked Telegram account and allows unlinking with confirmation", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getPreferences).mockResolvedValue(MOCK_PREFS_LINKED);
    vi.mocked(api.unlinkTelegram).mockResolvedValue({ ok: true, message: "Telegram успешно отвязан." });

    render(
      <ToastProvider>
        <PreferencesPage />
      </ToastProvider>
    );

    expect(await screen.findByText("@hr_user_tg")).toBeInTheDocument();
    const unlinkBtn = screen.getByTestId("unlink-telegram-btn");
    await user.click(unlinkBtn);

    expect(await screen.findByText("Отвязать Telegram?")).toBeInTheDocument();
    const confirmUnlink = screen.getByRole("button", { name: "Да, отвязать" });
    await user.click(confirmUnlink);

    expect(api.unlinkTelegram).toHaveBeenCalled();
  });

  it("updates email address and explicit consent toggle", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getPreferences).mockResolvedValue(MOCK_PREFS_UNLINKED);
    vi.mocked(api.savePreferences).mockResolvedValue({
      ...MOCK_PREFS_UNLINKED,
      email_address: "new_email@company.test",
      email_opt_in: true,
    });

    render(
      <ToastProvider>
        <PreferencesPage />
      </ToastProvider>
    );

    const emailInput = await screen.findByTestId("email-address-input");
    await user.clear(emailInput);
    await user.type(emailInput, "new_email@company.test");

    const consentToggle = screen.getByTestId("email-consent-toggle");
    await user.click(consentToggle);

    const saveBtn = screen.getByText("Сохранить настройки");
    await user.click(saveBtn);

    expect(api.savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({
        email_address: "new_email@company.test",
        email_opt_in: true,
      })
    );
  });
});
