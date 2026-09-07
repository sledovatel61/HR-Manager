import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  AdminChannels,
  EmailChannelStatus,
  IntegrationStatus,
  TelegramChannelStatus,
  User,
} from "../../types";
import { IntegrationsPage } from "./IntegrationsPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    getIntegrationStatus: vi.fn(),
    createTelegramLinkCode: vi.fn(),
    confirmTelegramLink: vi.fn(),
    unlinkTelegram: vi.fn(),
    updateTelegramConsent: vi.fn(),
    queueTelegramTest: vi.fn(),
    setNotificationEmail: vi.fn(),
    confirmNotificationEmail: vi.fn(),
    removeNotificationEmail: vi.fn(),
    updateEmailConsent: vi.fn(),
    fetchAdminChannels: vi.fn(),
    checkTelegramConfig: vi.fn(),
    checkSmtpConfig: vi.fn(),
    queueAdminSmtpTest: vi.fn(),
  };
});

import * as api from "../../api";

const HR_USER: User = {
  id: "00000000-0000-4000-8000-000000000001",
  username: "hr1",
  full_name: "HR One",
  role: "hr",
  is_active: true,
  locked_until: null,
  last_login_at: null,
  created_at: "2026-09-01T00:00:00Z",
};

const ADMIN_USER: User = { ...HR_USER, username: "admin", full_name: "Admin", role: "admin" };

const TG_UNLINKED: TelegramChannelStatus = {
  state: "not_configured",
  configured: false,
  linked: false,
  masked_chat_id: null,
  pending_confirmation: false,
  opt_in: false,
  consent_at: null,
  linked_at: null,
};

const EMAIL_EMPTY: EmailChannelStatus = {
  state: "not_configured",
  configured: false,
  verified: false,
  address_masked: null,
  pending_email_masked: null,
  pending_confirmation: false,
  opt_in: false,
  consent_at: null,
};

function statusOf(
  telegram: TelegramChannelStatus,
  email: EmailChannelStatus,
): IntegrationStatus {
  return { telegram, email };
}

const ADMIN_CHANNELS: AdminChannels = {
  telegram: { enabled: true, configured: true, bot_username: "hr_test_bot" },
  smtp: {
    enabled: true,
    configured: true,
    host: "mail.example.com",
    port: 587,
    encryption: "starttls",
    from_address: "noreply@example.com",
  },
};

const mockedStatus = vi.mocked(api.getIntegrationStatus);

function renderPage(user: User = HR_USER) {
  return render(
    <ToastProvider>
      <IntegrationsPage user={user} />
    </ToastProvider>,
  );
}

describe("IntegrationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows honest not-configured states when the server has no channels", async () => {
    mockedStatus.mockResolvedValue(statusOf(TG_UNLINKED, EMAIL_EMPTY));
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Telegram" })).toBeTruthy();
    });
    expect(screen.getAllByText("не настроено").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Получить ссылку для привязки")).toBeNull();
  });

  it("runs the telegram linking flow: link code, deep link, confirm", async () => {
    const user = userEvent.setup();
    mockedStatus.mockResolvedValue(
      statusOf({ ...TG_UNLINKED, configured: true }, EMAIL_EMPTY),
    );
    vi.mocked(api.createTelegramLinkCode).mockResolvedValue({
      deep_link: "https://t.me/hr_test_bot?start=RAWTOKEN",
      expires_at: "2026-09-07T12:15:00Z",
    });
    renderPage();

    await user.click(
      await screen.findByRole("button", { name: "Получить ссылку для привязки" }),
    );
    const deepLink = await screen.findByRole("link", { name: "Открыть бота в Telegram" });
    expect(deepLink.getAttribute("href")).toBe("https://t.me/hr_test_bot?start=RAWTOKEN");
    expect(deepLink.getAttribute("target")).toBe("_blank");

    mockedStatus.mockResolvedValue(
      statusOf(
        {
          ...TG_UNLINKED,
          configured: true,
          state: "works",
          linked: true,
          masked_chat_id: "***1234",
          linked_at: "2026-09-07T12:00:00Z",
        },
        EMAIL_EMPTY,
      ),
    );
    vi.mocked(api.confirmTelegramLink).mockResolvedValue({ linked: true, state: "works" });
    await user.click(
      screen.getByRole("button", { name: "Я нажал Start — подтвердить" }),
    );
    await waitFor(() => {
      expect(screen.getByText("***1234")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "Получать в Telegram" })).toBeTruthy();
  });

  it("toggles telegram consent without unlinking", async () => {
    const user = userEvent.setup();
    mockedStatus.mockResolvedValue(
      statusOf(
        {
          ...TG_UNLINKED,
          configured: true,
          state: "works",
          linked: true,
          masked_chat_id: "***1234",
          linked_at: "2026-09-07T12:00:00Z",
        },
        EMAIL_EMPTY,
      ),
    );
    vi.mocked(api.updateTelegramConsent).mockResolvedValue({
      channel: "telegram",
      opt_in: true,
      consent_at: "2026-09-07T12:00:00Z",
      policy_version: "phase9-v1",
    });
    renderPage();

    await user.click(
      await screen.findByRole("button", { name: "Получать в Telegram" }),
    );
    await waitFor(() => {
      expect(api.updateTelegramConsent).toHaveBeenCalledWith(true);
    });
  });

  it("runs the email flow: set address, enter token, confirm", async () => {
    const user = userEvent.setup();
    mockedStatus.mockResolvedValue(
      statusOf(TG_UNLINKED, { ...EMAIL_EMPTY, configured: true, state: "pending" }),
    );
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Указать адрес" }));
    await user.type(screen.getByLabelText("Адрес для уведомлений"), "hr1@example.com");
    mockedStatus.mockResolvedValue(
      statusOf(
        TG_UNLINKED,
        {
          ...EMAIL_EMPTY,
          configured: true,
          state: "pending",
          pending_email_masked: "h***@example.com",
          pending_confirmation: true,
        },
      ),
    );
    vi.mocked(api.setNotificationEmail).mockResolvedValue({
      pending_email_masked: "h***@example.com",
      expires_at: "2026-09-08T12:00:00Z",
      verification_queued: true,
    });
    await user.click(screen.getByRole("button", { name: "Отправить код" }));
    await waitFor(() => {
      expect(api.setNotificationEmail).toHaveBeenCalledWith("hr1@example.com");
    });

    await user.type(await screen.findByLabelText("Код из письма"), "tokentokentoken");
    mockedStatus.mockResolvedValue(
      statusOf(
        TG_UNLINKED,
        {
          ...EMAIL_EMPTY,
          configured: true,
          state: "works",
          verified: true,
          address_masked: "h***@example.com",
        },
      ),
    );
    vi.mocked(api.confirmNotificationEmail).mockResolvedValue({
      verified: true,
      address_masked: "h***@example.com",
    });
    await user.click(screen.getByRole("button", { name: "Подтвердить адрес" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Получать письма" })).toBeTruthy();
    });
  });

  it("hides the admin checks from non-admin roles", async () => {
    mockedStatus.mockResolvedValue(statusOf(TG_UNLINKED, EMAIL_EMPTY));
    renderPage(HR_USER);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Telegram" })).toBeTruthy();
    });
    expect(screen.queryByRole("heading", { name: "Проверка каналов (администратор)" })).toBeNull();
    expect(api.fetchAdminChannels).not.toHaveBeenCalled();
  });

  it("shows admin channel checks without secrets", async () => {
    const user = userEvent.setup();
    mockedStatus.mockResolvedValue(statusOf(TG_UNLINKED, EMAIL_EMPTY));
    vi.mocked(api.fetchAdminChannels).mockResolvedValue(ADMIN_CHANNELS);
    vi.mocked(api.checkTelegramConfig).mockResolvedValue({
      ok: true,
      detail: "Бот hr_test_bot отвечает.",
      error_class: null,
      bot_username: "hr_test_bot",
    });
    renderPage(ADMIN_USER);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Проверка каналов (администратор)" }),
      ).toBeTruthy();
    });
    expect(screen.getByText("hr_test_bot")).toBeTruthy();
    expect(screen.getByText(/mail\.example\.com:587/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Проверить Telegram" }));
    await waitFor(() => {
      expect(api.checkTelegramConfig).toHaveBeenCalled();
    });
  });
});
