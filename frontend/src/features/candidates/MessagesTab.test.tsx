import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, CandidateChannels, CandidateMessageList, User } from "../../types";
import { MessagesTab } from "./MessagesTab";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    getCandidateChannels: vi.fn(),
    updateCandidateChannelConsent: vi.fn(),
    initiateCandidateEmailConfirmation: vi.fn(),
    createCandidateTelegramInvite: vi.fn(),
    confirmCandidateTelegram: vi.fn(),
    unlinkCandidateTelegram: vi.fn(),
    listCandidateMessages: vi.fn(),
    previewCandidateMessage: vi.fn(),
    sendCandidateMessage: vi.fn(),
    cancelCandidateMessage: vi.fn(),
    listEvents: vi.fn(),
  };
});

import * as api from "../../api";

const HR: User = {
  id: "22222222-2222-2222-2222-222222222222",
  username: "hr1",
  full_name: "HR Один",
  role: "hr",
  is_active: true,
  locked_until: null,
  last_login_at: null,
  created_at: "2026-09-01T10:00:00Z",
};

const CANDIDATE: Candidate = {
  id: "44444444-4444-4444-4444-444444444444",
  full_name: "Петров Пётр Петрович",
  phone: "+7 900 123-45-67",
  email: "petrov@example.com",
  source: "site",
  position: "Инженер",
  owner_user_id: HR.id,
  owner_username: "hr1",
  stage: "new",
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

const CHANNELS: CandidateChannels = {
  email: {
    channel: "email",
    state: "allowed",
    configured: true,
    target_masked: "p***@example.com",
    has_target: true,
    invite_active: false,
    consent: {
      channel: "email",
      granted: true,
      granted_at: "2026-09-02T09:00:00Z",
      source: "hr_recorded",
      policy_version: "phase10-v1",
    },
  },
  telegram: {
    channel: "telegram",
    state: "not_connected",
    configured: true,
    target_masked: null,
    has_target: false,
    invite_active: false,
    consent: null,
  },
  allowed_channels: ["email"],
};

const MESSAGES: CandidateMessageList = {
  items: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      message_type: "document_request",
      channel: "email",
      status: "accepted",
      source: "manual",
      title: "Запрос документов",
      body: "Здравствуйте, Петров Пётр Петрович!\n— Паспорт РФ",
      event_id: null,
      initiator_user_id: "22222222-2222-2222-2222-222222222222",
      initiator_username: "hr1",
      scheduled_at: "2026-09-02T10:01:00Z",
      scheduled_at_effective: "2026-09-02T10:01:00Z",
      queued_at: "2026-09-02T10:01:00Z",
      accepted_at: "2026-09-02T10:01:05Z",
      delivered_at: null,
      failed_at: null,
      cancelled_at: null,
      attempts: 1,
      next_attempt_at: null,
      error_code: null,
      error_class: null,
      provider_message_id: "abc-1",
    },
    {
      id: "22222222-1111-1111-1111-111111111111",
      message_type: "document_reminder",
      channel: "telegram",
      status: "queued",
      source: "system",
      title: "Напоминание о недостающих документах",
      body: "Здравствуйте! Напоминаем про документы.",
      event_id: null,
      initiator_user_id: null,
      initiator_username: null,
      scheduled_at: null,
      scheduled_at_effective: null,
      queued_at: "2026-09-03T10:00:00Z",
      accepted_at: null,
      delivered_at: null,
      failed_at: null,
      cancelled_at: null,
      attempts: 0,
      next_attempt_at: null,
      error_code: null,
      error_class: null,
      provider_message_id: null,
    },
    {
      id: "33333333-1111-1111-1111-111111111111",
      message_type: "candidate_email_confirm",
      channel: "email",
      status: "accepted",
      source: "system",
      title: "Подтвердите согласие на сообщения по почте",
      body: "Здравствуйте! Перейдите по ссылке: (ссылка подтверждения отправлена кандидату в письме)",
      event_id: null,
      initiator_user_id: "22222222-2222-2222-2222-222222222222",
      initiator_username: "hr1",
      scheduled_at: null,
      scheduled_at_effective: null,
      queued_at: "2026-09-04T10:00:00Z",
      accepted_at: "2026-09-04T10:00:05Z",
      delivered_at: null,
      failed_at: null,
      cancelled_at: null,
      attempts: 1,
      next_attempt_at: null,
      error_code: null,
      error_class: null,
      provider_message_id: null,
    },
  ],
  total: 3,
  limit: 20,
  offset: 0,
};

function renderTab() {
  return render(
    <ToastProvider>
      <MessagesTab candidate={CANDIDATE} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getCandidateChannels).mockResolvedValue(CHANNELS);
  vi.mocked(api.listCandidateMessages).mockResolvedValue(MESSAGES);
  vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 30, offset: 0 });
});

describe("MessagesTab", () => {
  it("shows both channel states with masked targets and consent", async () => {
    renderTab();

    const section = await screen.findByRole("region", {
      name: "Каналы связи с кандидатом",
    });
    const emailCard = within(section).getAllByText("Электронная почта")[0].closest("div");
    expect(emailCard).not.toBeNull();
    expect(within(section).getByText("Разрешён")).toBeInTheDocument();
    expect(within(section).getByText("Адресат: p***@example.com")).toBeInTheDocument();
    expect(within(section).getByText(/Согласие: есть/)).toBeInTheDocument();
    expect(within(section).getByText(/записал HR/)).toBeInTheDocument();

    expect(within(section).getByText("Не подключён")).toBeInTheDocument();
    expect(within(section).getByText("Чат не подключён.")).toBeInTheDocument();
    expect(
      screen.queryByText("В карточке не указан адрес электронной почты.")
    ).toBeNull();
  });

  it("initiates the email double opt-in letter and revokes via the API", async () => {
    const user = userEvent.setup();
    vi.mocked(api.initiateCandidateEmailConfirmation).mockResolvedValue({
      queued: true,
      email_masked: "p***@example.com",
      expires_at: "2026-09-03T12:30:00Z",
    });
    const revoked: CandidateChannels = {
      ...CHANNELS,
      email: {
        ...CHANNELS.email,
        state: "forbidden",
        consent: { ...CHANNELS.email.consent!, granted: false },
      },
      allowed_channels: [],
    };
    vi.mocked(api.getCandidateChannels)
      .mockResolvedValueOnce(CHANNELS)
      .mockResolvedValueOnce(revoked)
      .mockResolvedValue(revoked);

    renderTab();
    await screen.findByText("Разрешён");

    // The granted channel offers no new letter (already confirmed).
    expect(
      screen.queryByRole("button", { name: "Отправить письмо подтверждения" })
    ).toBeDisabled();

    // Revoke (the only HR power over the email consent).
    await user.click(screen.getByRole("button", { name: "Отозвать согласие" }));
    await waitFor(() =>
      expect(api.updateCandidateChannelConsent).toHaveBeenCalledWith(
        CANDIDATE.id,
        "email",
        false
      )
    );
    expect(await screen.findByText("Запрещён")).toBeInTheDocument();

    // On a non-allowed channel the HR may only send the confirmation letter.
    await user.click(screen.getByRole("button", { name: "Отправить письмо подтверждения" }));
    await waitFor(() =>
      expect(api.initiateCandidateEmailConfirmation).toHaveBeenCalledWith(CANDIDATE.id)
    );
    expect(
      await screen.findByText(/Письмо подтверждения отправлено на p\*\*\*@example\.com/)
    ).toBeInTheDocument();
  });

  it("creates a Telegram invitation and shows the deep link once", async () => {
    const user = userEvent.setup();
    vi.mocked(api.createCandidateTelegramInvite).mockResolvedValue({
      deep_link: "https://t.me/hr_test_bot?start=abc123",
      expires_at: "2026-09-03T12:30:00Z",
    });
    vi.mocked(api.confirmCandidateTelegram).mockResolvedValue({
      linked: true,
      state: "pending",
    });

    renderTab();
    await screen.findByText("Не подключён");

    await user.click(screen.getByRole("button", { name: "Приглашение" }));

    expect(await screen.findByText(/t\.me\/hr_test_bot\?start=abc123/)).toBeInTheDocument();
    expect(api.createCandidateTelegramInvite).toHaveBeenCalledWith(CANDIDATE.id);

    await user.click(screen.getByRole("button", { name: "Проверить подключение" }));
    await waitFor(() => expect(api.confirmCandidateTelegram).toHaveBeenCalledWith(CANDIDATE.id));
  });

  it("previews a document request without ever sending a recipient or text", async () => {
    const user = userEvent.setup();
    vi.mocked(api.previewCandidateMessage).mockResolvedValue({
      title: "Запрос документов",
      body: "Здравствуйте, Петров Пётр Петрович!\n— Паспорт РФ",
      channels: ["email"],
    });

    renderTab();
    await screen.findByText("История отправок");

    // document_request is the default type; documents are required first.
    expect(screen.getByRole("button", { name: /Предпросмотр/ })).toBeDisabled();

    const documentsField = await screen.findByLabelText(/Документы/);
    await user.type(documentsField, "Паспорт РФ{enter}СНИЛС");

    const previewButton = screen.getByRole("button", { name: /Предпросмотр/ });
    expect(previewButton).toBeEnabled();
    await user.click(previewButton);

    await waitFor(() => expect(api.previewCandidateMessage).toHaveBeenCalled());
    // The client passes only the closed vocabulary — no address, chat id or text.
    expect(api.previewCandidateMessage).toHaveBeenCalledWith(CANDIDATE.id, {
      message_type: "document_request",
      documents: ["Паспорт РФ", "СНИЛС"],
    });
    expect(await screen.findByText(/Уйдёт по каналам: Электронная почта/)).toBeInTheDocument();
  });

  it("retries a failed send with the same idempotency key and refreshes the history", async () => {
    const user = userEvent.setup();
    const success = { messages: [], channels: ["email" as const] };
    // The first attempt fails on the transport level: the form stays filled,
    // so the retry must reuse the same idempotency key.
    vi.mocked(api.sendCandidateMessage)
      .mockRejectedValueOnce(new api.ApiError(0, "network error"))
      .mockResolvedValue(success);

    renderTab();
    await screen.findByText("История отправок");

    const documentsField = await screen.findByLabelText(/Документы/);
    await user.type(documentsField, "Паспорт РФ");

    await user.click(screen.getByRole("button", { name: /^Отправить$/ }));
    expect(await screen.findByText("network error")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Отправить$/ }));
    await waitFor(() => expect(vi.mocked(api.sendCandidateMessage).mock.calls.length).toBe(2));
    const [firstId, firstPayload] = vi.mocked(api.sendCandidateMessage).mock.calls[0];
    const secondPayload = vi.mocked(api.sendCandidateMessage).mock.calls[1][1];
    expect(firstId).toBe(CANDIDATE.id);
    expect(firstPayload).toMatchObject({
      message_type: "document_request",
      documents: ["Паспорт РФ"],
    });
    expect(typeof firstPayload.idempotency_key).toBe("string");
    expect(firstPayload.idempotency_key.length).toBeGreaterThanOrEqual(8);
    // The same payload retried -> the same operation -> the same key.
    expect(secondPayload.idempotency_key).toBe(firstPayload.idempotency_key);

    // The successful send clears the form; the next composition is a new
    // operation: a fresh key and still no location field.
    await user.type(documentsField, "СНИЛС");
    await user.click(screen.getByRole("button", { name: /^Отправить$/ }));
    await waitFor(() => expect(vi.mocked(api.sendCandidateMessage).mock.calls.length).toBe(3));
    const thirdPayload = vi.mocked(api.sendCandidateMessage).mock.calls[2][1];
    expect(thirdPayload).toMatchObject({
      message_type: "document_request",
      documents: ["СНИЛС"],
    });
    expect("location" in thirdPayload).toBe(false);
    expect(thirdPayload.idempotency_key).not.toBe(firstPayload.idempotency_key);
    // The history reloaded after the send.
    await waitFor(() =>
      expect(vi.mocked(api.listCandidateMessages).mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });

  it("surfaces the backend error when the send is refused", async () => {
    const user = userEvent.setup();
    vi.mocked(api.sendCandidateMessage).mockRejectedValue(
      new api.ApiError(409, "Такое сообщение уже ожидает отправки этому кандидату.")
    );

    renderTab();
    await screen.findByText("История отправок");

    const documentsField = await screen.findByLabelText(/Документы/);
    await user.type(documentsField, "Паспорт РФ");

    await user.click(screen.getByRole("button", { name: /^Отправить$/}));

    expect(
      await screen.findByText("Такое сообщение уже ожидает отправки этому кандидату.")
    ).toBeInTheDocument();
  });

  it("requires an interview event for interview message types", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listEvents).mockResolvedValue({
      items: [
        {
          id: "99999999-9999-9999-9999-999999999999",
          candidate_id: CANDIDATE.id,
          candidate_full_name: CANDIDATE.full_name,
          type: "interview",
          title: "Первичное интервью",
          note: null,
          status: "scheduled",
          starts_at: "2026-09-10T10:00:00Z",
          ends_at: null,
          remind_at: null,
          completed_at: null,
          author_user_id: HR.id,
          author_username: "hr1",
          assignee_user_id: HR.id,
          assignee_username: "hr1",
          version: 1,
          created_at: "2026-09-01T10:00:00Z",
          updated_at: "2026-09-01T10:00:00Z",
        },
      ],
      total: 1,
      limit: 30,
      offset: 0,
    });

    renderTab();
    await screen.findByText("История отправок");

    await user.selectOptions(screen.getByLabelText(/Тип сообщения/), "interview_scheduled");

    expect(screen.getByLabelText(/Собеседование/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Предпросмотр/ })).toBeDisabled();
    expect(screen.getByText("Выберите собеседование.")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText(/Собеседование/),
      "99999999-9999-9999-9999-999999999999"
    );
    expect(screen.getByRole("button", { name: /Предпросмотр/ })).toBeEnabled();
  });

  it("renders the immutable history with status badges and cancels a queued message", async () => {
    const user = userEvent.setup();
    vi.mocked(api.cancelCandidateMessage).mockResolvedValue({
      id: "22222222-1111-1111-1111-111111111111",
      status: "cancelled",
    });

    renderTab();

    const history = await screen.findByRole("region", { name: "История сообщений" });
    expect(within(history).getByText("Запрос документов")).toBeInTheDocument();
    expect(within(history).getAllByText("Принято провайдером").length).toBeGreaterThanOrEqual(1);
    expect(within(history).getByText("В очереди")).toBeInTheDocument();
    // The exact stored text and the initiator are part of the history.
    expect(within(history).getByText(/— Паспорт РФ/)).toBeInTheDocument();
    expect(within(history).getAllByText(/инициатор: hr1/).length).toBeGreaterThanOrEqual(1);
    expect(within(history).getByText(/инициатор: система/)).toBeInTheDocument();
    // The double opt-in letter is listed, but its confirmation link is
    // masked — the HR must never be able to click it.
    const confirmLetter = within(history).getByText(
      "Подтвердите согласие на сообщения по почте"
    );
    expect(confirmLetter).toBeInTheDocument();
    expect(within(history).getByText(/ссылка подтверждения отправлена кандидату/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("token=");

    await user.click(within(history).getByRole("button", { name: "Отменить отправку" }));

    await waitFor(() =>
      expect(api.cancelCandidateMessage).toHaveBeenCalledWith(
        CANDIDATE.id,
        "22222222-1111-1111-1111-111111111111"
      )
    );
  });

  it("marks non-allowed channels as disabled in the channel picker", async () => {
    renderTab();
    await screen.findByText("История отправок");

    const channelSelect = screen.getByLabelText("Канал");
    const telegramOption = within(channelSelect).getByRole("option", {
      name: /Telegram \(не разрешён\)/,
    }) as HTMLOptionElement;
    expect(telegramOption.disabled).toBe(true);

    const emailOption = within(channelSelect).getByRole("option", {
      name: "Электронная почта",
    }) as HTMLOptionElement;
    expect(emailOption.disabled).toBe(false);
  });
});
