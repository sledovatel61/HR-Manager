import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { ToastProvider } from "../../design-system/components/Toast";
import type { IntegrationStatusResponse, QueueDiagnostics, SetupState } from "../../types";
import { AdminQueuePage } from "./AdminQueuePage";

vi.mock("../../api");

const MOCK_QUEUE_DIAG: QueueDiagnostics = {
  counts: {
    queued: 2,
    sending: 1,
    accepted: 5,
    delivered: 12,
    failed: 0,
    cancelled: 0,
    skipped: 1,
  },
  oldest_queued_at: "2026-09-07T12:00:00Z",
  stuck_sending: 0,
  worker: { alive: true, processed_total: 20, failed_total: 0 },
};

const MOCK_SETUP: SetupState = {
  pilot_exists: true,
  pilot_grant_active: true,
  preferences_initialized: true,
  worker_alive: true,
  channels: {
    telegram: "working",
    email: "working",
  },
};

const MOCK_INTEG_STATUS: IntegrationStatusResponse = {
  telegram: {
    status: "working",
    configured_in_system: true,
    linked: true,
    opt_in: true,
    has_consent: true,
    details: { bot_username: "HrManagerBot", username: "admin_tg" },
  },
  email: {
    status: "working",
    configured_in_system: true,
    linked: true,
    opt_in: true,
    has_consent: true,
    details: { address: "admin@company.test" },
  },
  in_app: {
    status: "working",
    configured_in_system: true,
    linked: true,
    opt_in: true,
    has_consent: true,
  },
};

describe("AdminQueuePage (Phase 9 Probes and Test Send)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchQueueDiagnostics).mockResolvedValue(MOCK_QUEUE_DIAG);
    vi.mocked(api.fetchSetupState).mockResolvedValue(MOCK_SETUP);
    vi.mocked(api.listAccessGrants).mockResolvedValue({ items: [] });
    vi.mocked(api.fetchIntegrationsStatus).mockResolvedValue(MOCK_INTEG_STATUS);
  });

  it("renders queue diagnostics and integration channel statuses", async () => {
    render(
      <ToastProvider>
        <AdminQueuePage />
      </ToastProvider>
    );

    expect(await screen.findByText("Состояние очереди и worker")).toBeInTheDocument();
    expect(screen.getByText("Каналы интеграций и диагностика подключения")).toBeInTheDocument();
    expect(screen.getByText("Тестовая отправка сообщения")).toBeInTheDocument();
  });

  it("runs Telegram test connection probe and displays bot info", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminTestTelegramConnection).mockResolvedValue({
      ok: true,
      bot_id: 998877,
      bot_username: "HrManagerBot",
      first_name: "HR Bot",
    });

    render(
      <ToastProvider>
        <AdminQueuePage />
      </ToastProvider>
    );

    const probeBtn = await screen.findByRole("button", { name: "Проверить подключение getMe" });
    await user.click(probeBtn);

    expect(api.adminTestTelegramConnection).toHaveBeenCalled();
    expect(await screen.findByText(/Бот подключен: @HrManagerBot/)).toBeInTheDocument();
  });

  it("runs SMTP test connection probe and displays connection details", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminTestSmtpConnection).mockResolvedValue({
      ok: true,
      host: "smtp.company.test",
      port: 587,
      use_tls: false,
      use_starttls: true,
      authenticated: true,
    });

    render(
      <ToastProvider>
        <AdminQueuePage />
      </ToastProvider>
    );

    const probeBtn = await screen.findByRole("button", { name: "Проверить подключение NOOP" });
    await user.click(probeBtn);

    expect(api.adminTestSmtpConnection).toHaveBeenCalled();
    expect(await screen.findByText(/SMTP сервер отвечает: smtp\.company\.test:587/)).toBeInTheDocument();
  });

  it("submits test send and renders provider acceptance result", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminTestSend).mockResolvedValue({
      ok: true,
      channel: "telegram",
      provider_message_id: "tg-msg-test-123",
      message: "Тестовое сообщение принято Telegram Bot API.",
    });

    render(
      <ToastProvider>
        <AdminQueuePage />
      </ToastProvider>
    );

    const sendBtn = await screen.findByRole("button", { name: "Отправить тест" });
    await user.click(sendBtn);

    expect(api.adminTestSend).toHaveBeenCalled();
    expect(await screen.findByText("Результат отправки:")).toBeInTheDocument();
    expect(screen.getByText(/ID сообщения провайдера \(Message-ID\): tg-msg-test-123/)).toBeInTheDocument();
  });
});
