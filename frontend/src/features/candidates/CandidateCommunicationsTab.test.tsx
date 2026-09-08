import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, CandidateChannelStatus, CandidateMessage } from "../../types";
import { CandidateCommunicationsTab } from "./CandidateCommunicationsTab";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    fetchCandidateChannels: vi.fn(),
    requestCandidateEmailConsent: vi.fn(),
    createCandidateTelegramLink: vi.fn(),
    confirmCandidateTelegram: vi.fn(),
    revokeCandidateChannel: vi.fn(),
    previewCandidateMessage: vi.fn(),
    sendCandidateMessage: vi.fn(),
    listCandidateMessages: vi.fn(),
    cancelCandidateMessage: vi.fn(),
    listEvents: vi.fn(),
  };
});

import * as api from "../../api";

const CANDIDATE: Candidate = {
  id: "44444444-4444-4444-4444-444444444444",
  full_name: "Петров Пётр Петрович",
  phone: "+7 900 123-45-67",
  email: "petrov@example.com",
  source: "site",
  position: "Инженер",
  owner_user_id: "22222222-2222-2222-2222-222222222222",
  owner_username: "hr1",
  stage: "interview_scheduled",
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

function emailChannel(overrides: Partial<CandidateChannelStatus> = {}): CandidateChannelStatus {
  return {
    channel: "email",
    state: "allowed",
    reason: null,
    recipient_masked: "p***@example.com",
    consent_at: "2026-09-02T08:00:00Z",
    consent_source: "candidate_email_link",
    pending_expires_at: null,
    ...overrides,
  };
}

function telegramChannel(overrides: Partial<CandidateChannelStatus> = {}): CandidateChannelStatus {
  return {
    channel: "telegram",
    state: "allowed",
    reason: null,
    recipient_masked: "•1234",
    consent_at: "2026-09-03T09:00:00Z",
    consent_source: "telegram_start",
    pending_expires_at: null,
    ...overrides,
  };
}

function historyRow(overrides: Partial<CandidateMessage> = {}): CandidateMessage {
  return {
    id: "m-1",
    channel: "email",
    message_type: "documents_request",
    source: "manual",
    title: "Запрос документов",
    body: "Здравствуйте, Пётр! Нужны документы.",
    status: "queued",
    attempts: 0,
    event_id: null,
    initiator_user_id: "22222222-2222-2222-2222-222222222222",
    scheduled_at: "2026-09-08T10:00:00Z",
    scheduled_at_effective: null,
    queued_at: "2026-09-08T10:00:00Z",
    started_at: null,
    accepted_at: null,
    delivered_at: null,
    failed_at: null,
    cancelled_at: null,
    error_class: null,
    provider_message_id: null,
    created_at: "2026-09-08T10:00:00Z",
    updated_at: "2026-09-08T10:00:00Z",
    recipient_masked: "p***@example.com",
    ...overrides,
  };
}

function renderTab() {
  return render(
    <ToastProvider>
      <CandidateCommunicationsTab candidate={CANDIDATE} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fetchCandidateChannels).mockResolvedValue({
    channels: [emailChannel(), telegramChannel()],
  });
  vi.mocked(api.listCandidateMessages).mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
  vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
  vi.mocked(api.previewCandidateMessage).mockResolvedValue({
    title: "Запрос документов",
    body: "Здравствуйте, Пётр! Нужны документы.\nПаспорт",
    quiet_hours_now: false,
    will_send: true,
  });
  vi.mocked(api.sendCandidateMessage).mockResolvedValue({
    message: historyRow({ status: "queued" }),
    duplicate: false,
  });
  vi.mocked(api.cancelCandidateMessage).mockResolvedValue({
    message: historyRow({ status: "cancelled", cancelled_at: "2026-09-08T11:00:00Z" }),
    cancelled: true,
  });
  vi.mocked(api.revokeCandidateChannel).mockResolvedValue({ channel: "email", state: "denied" });
  vi.mocked(api.requestCandidateEmailConsent).mockResolvedValue({
    message_id: "m-consent",
    state: "pending_confirmation",
    expires_at: "2026-09-11T10:00:00Z",
  });
  vi.mocked(api.createCandidateTelegramLink).mockResolvedValue({
    deep_link: "https://t.me/hr_bot?start=secret",
    expires_at: "2026-09-08T10:10:00Z",
    state: "pending_confirmation",
  });
  vi.mocked(api.confirmCandidateTelegram).mockResolvedValue({
    state: "allowed",
    detail: "Telegram-канал подключён.",
  });
});

describe("CandidateCommunicationsTab", () => {
  it("shows channel states and the empty history", async () => {
    renderTab();

    expect(await screen.findByText("Каналы и согласия")).toBeInTheDocument();
    expect(screen.getAllByText("Разрешён")).toHaveLength(2);
    expect(screen.getByText("p***@example.com")).toBeInTheDocument();
    expect(await screen.findByText("Сообщений пока не было")).toBeInTheDocument();
    // The honest warning about provider acceptance is always visible.
    expect(
      screen.getByText(/не означает доставку, открытие или прочтение/)
    ).toBeInTheDocument();
  });

  it("previews and queues a document message without sending any recipient/text to the client contract", async () => {
    renderTab();

    const textarea = await screen.findByLabelText("Список документов");
    await userEvent.type(textarea, "Паспорт\nСНИЛС");
    await userEvent.click(screen.getByRole("button", { name: "Показать предпросмотр" }));

    await waitFor(() =>
      expect(api.previewCandidateMessage).toHaveBeenCalledWith(CANDIDATE.id, {
        message_type: "documents_request",
        channel: "email",
        event_id: null,
        documents: ["Паспорт", "СНИЛС"],
        confirm_quiet_hours: false,
      })
    );
    expect(await screen.findByText(/Нужны документы/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
    await waitFor(() =>
      expect(api.sendCandidateMessage).toHaveBeenCalledWith(
        CANDIDATE.id,
        expect.objectContaining({
          message_type: "documents_request",
          channel: "email",
          documents: ["Паспорт", "СНИЛС"],
          confirm_quiet_hours: false,
          idempotency_key: expect.any(String),
        })
      )
    );
    // The sent message appears in the history (list was refetched).
    await waitFor(() => expect(api.listCandidateMessages).toHaveBeenCalledTimes(2));
  });

  it("asks for explicit confirmation when the server reports quiet hours", async () => {
    vi.mocked(api.previewCandidateMessage).mockResolvedValue({
      title: "Запрос документов",
      body: "Текст письма",
      quiet_hours_now: true,
      will_send: true,
    });
    renderTab();

    await userEvent.type(await screen.findByLabelText("Список документов"), "Паспорт");
    await userEvent.click(screen.getByRole("button", { name: "Показать предпросмотр" }));

    const sendButton = await screen.findByRole("button", { name: "Поставить в очередь" });
    expect(sendButton).toBeDisabled();

    await userEvent.click(await screen.findByRole("checkbox"));
    expect(sendButton).toBeEnabled();

    await userEvent.click(sendButton);
    await waitFor(() =>
      expect(api.sendCandidateMessage).toHaveBeenCalledWith(
        CANDIDATE.id,
        expect.objectContaining({ confirm_quiet_hours: true })
      )
    );
  });

  it("requests the email double-opt-in consent and shows the pending state", async () => {
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
        telegramChannel({ state: "denied", recipient_masked: null, consent_at: null, consent_source: null }),
      ],
    });
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel({
          state: "pending_confirmation",
          recipient_masked: null,
          consent_at: null,
          consent_source: null,
          pending_expires_at: "2026-09-11T10:00:00Z",
        }),
        telegramChannel({ state: "denied", recipient_masked: null, consent_at: null, consent_source: null }),
      ],
    });
    renderTab();

    await userEvent.click(
      await screen.findByRole("button", { name: "Запросить согласие по email" })
    );

    await waitFor(() =>
      expect(api.requestCandidateEmailConsent).toHaveBeenCalledWith(CANDIDATE.id)
    );
    expect(await screen.findByText(/Ждём подтверждения по ссылке из письма/)).toBeInTheDocument();
  });

  it("revokes an allowed email channel through the confirm dialog", async () => {
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel(),
        telegramChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
      ],
    });
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel({
          state: "denied",
          recipient_masked: null,
          consent_at: null,
          consent_source: null,
        }),
        telegramChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
      ],
    });
    renderTab();

    await userEvent.click(
      await screen.findByRole("button", { name: "Отозвать согласие" })
    );
    const dialog = screen.getByRole("alertdialog");
    expect(
      within(dialog).getByText("Отозвать согласие на email-канал?")
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Отозвать согласие" }));

    await waitFor(() =>
      expect(api.revokeCandidateChannel).toHaveBeenCalledWith(CANDIDATE.id, "email")
    );
    expect(await screen.findByText("Запрещён")).toBeInTheDocument();
  });

  it("creates a Telegram linking code, shows the one-time link and confirms the binding", async () => {
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
        telegramChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
      ],
    });
    // After creating the code the server still reports pending confirmation.
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
        telegramChannel({
          state: "pending_confirmation",
          recipient_masked: null,
          consent_at: null,
          consent_source: null,
          pending_expires_at: "2026-09-08T10:10:00Z",
        }),
      ],
    });
    // After a successful check the channel is allowed.
    vi.mocked(api.fetchCandidateChannels).mockResolvedValueOnce({
      channels: [
        emailChannel({ state: "not_connected", recipient_masked: null, consent_at: null, consent_source: null }),
        telegramChannel(),
      ],
    });
    renderTab();

    await userEvent.click(
      await screen.findByRole("button", { name: "Подключить Telegram" })
    );
    await waitFor(() => expect(api.createCandidateTelegramLink).toHaveBeenCalledWith(CANDIDATE.id));
    expect(await screen.findByText("https://t.me/hr_bot?start=secret")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Проверить подключение" }));
    await waitFor(() => expect(api.confirmCandidateTelegram).toHaveBeenCalledWith(CANDIDATE.id));
    // After the check the channel becomes allowed (channels were refetched).
    expect(await screen.findAllByText("Разрешён")).toHaveLength(1);
  });

  it("requires an interview for interview messages and never calls preview without one", async () => {
    renderTab();

    await userEvent.selectOptions(
      await screen.findByLabelText("Тип сообщения"),
      "interview_rescheduled"
    );
    expect(screen.getByRole("option", { name: "Нет подходящих собеседований" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Показать предпросмотр" }));
    expect(
      await screen.findByText("Выберите собеседование — текст сообщения формируется сервером из события.")
    ).toBeInTheDocument();
    expect(api.previewCandidateMessage).not.toHaveBeenCalled();
  });

  it("offers an upcoming interview from the server list for an interview message", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({
      items: [
        {
          id: "ev-1",
          candidate_id: CANDIDATE.id,
          candidate_full_name: "Петров Пётр Петрович",
          type: "interview",
          title: "Собеседование с руководителем",
          note: null,
          status: "scheduled",
          starts_at: "2026-10-05T10:00:00Z",
          ends_at: null,
          remind_at: null,
          completed_at: null,
          author_user_id: "22222222-2222-2222-2222-222222222222",
          author_username: "hr1",
          assignee_user_id: "22222222-2222-2222-2222-222222222222",
          assignee_username: "hr1",
          version: 1,
          created_at: "2026-09-01T10:00:00Z",
          updated_at: "2026-09-01T10:00:00Z",
        },
      ],
      total: 1,
      limit: 100,
      offset: 0,
    });
    renderTab();

    await userEvent.selectOptions(
      await screen.findByLabelText("Тип сообщения"),
      "interview_reminder"
    );
    const eventSelect = await screen.findByLabelText(/^Собеседование/);
    expect(eventSelect).not.toBeDisabled();
    await userEvent.selectOptions(eventSelect, "ev-1");
    await userEvent.click(screen.getByRole("button", { name: "Показать предпросмотр" }));

    await waitFor(() =>
      expect(api.previewCandidateMessage).toHaveBeenCalledWith(
        CANDIDATE.id,
        expect.objectContaining({ message_type: "interview_reminder", event_id: "ev-1" })
      )
    );
  });

  it("cancels a queued message from the history", async () => {
    vi.mocked(api.listCandidateMessages).mockResolvedValueOnce({
      items: [historyRow()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderTab();

    await userEvent.click(await screen.findByRole("button", { name: "Отменить отправку" }));
    await waitFor(() => expect(api.cancelCandidateMessage).toHaveBeenCalledWith(CANDIDATE.id, "m-1"));
    expect(await screen.findByText("Сообщений пока не было")).toBeInTheDocument();
  });

  it("retries after a channel-load failure", async () => {
    vi.mocked(api.fetchCandidateChannels).mockRejectedValueOnce(new api.ApiError(500, "Сбой"));
    renderTab();

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить попытку" }));

    expect(await screen.findAllByText("Разрешён")).toHaveLength(2);
  });

  it("shows a placeholder and does not call the API for deleted candidates", async () => {
    render(
      <ToastProvider>
        <CandidateCommunicationsTab candidate={{ ...CANDIDATE, is_deleted: true }} />
      </ToastProvider>
    );

    expect(await screen.findByText("Кандидат в архиве")).toBeInTheDocument();
    expect(api.fetchCandidateChannels).not.toHaveBeenCalled();
  });
});
