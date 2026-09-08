/** «Сообщения» в карточке кандидата (phase 10): каналы и согласия, ручная
 * отправка с серверным предпросмотром, неизменяемая история.
 *
 * Recipient, chat id, email, текст и владелец ВСЕГДА определяются сервером —
 * форма передаёт только тип сообщения, канал, событие и список документов.
 * «accepted» — только техническое принятие провайдером; интерфейс никогда не
 * утверждает доставку или прочтение.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  cancelCandidateMessage,
  confirmCandidateTelegram,
  createCandidateTelegramLink,
  fetchCandidateChannels,
  listCandidateMessages,
  listEvents,
  previewCandidateMessage,
  requestCandidateEmailConsent,
  revokeCandidateChannel,
  sendCandidateMessage,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput } from "../../design-system/components/Field";
import { Badge } from "../../design-system/components/StatusChip";
import { ErrorState, EmptyState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import { Icon } from "../../design-system/icons/Icon";
import {
  CANDIDATE_CHANNEL_LABELS,
  CANDIDATE_MESSAGE_SOURCE_LABELS,
  CANDIDATE_MESSAGE_STATUS_LABELS,
  CANDIDATE_MESSAGE_TYPE_LABELS,
  type CalendarEvent,
  type Candidate,
  type CandidateChannel,
  type CandidateChannelReason,
  type CandidateChannelState,
  type CandidateChannelStatus,
  type CandidateManualMessageType,
  type CandidateMessage,
  type CandidateMessageSendInput,
  type CandidateMessageType,
  type StageTone,
} from "../../types";
import { formatDateTime } from "./format";
import "./candidate-communications.css";

const HISTORY_PAGE_SIZE = 20;

/** Six business kinds an HR may queue manually (consent_invite is internal). */
const MANUAL_TYPES: CandidateManualMessageType[] = [
  "interview_scheduled",
  "interview_reminder",
  "interview_rescheduled",
  "interview_cancelled",
  "documents_request",
  "documents_reminder",
];

const INTERVIEW_TYPES = new Set<CandidateMessageType>([
  "interview_scheduled",
  "interview_reminder",
  "interview_rescheduled",
  "interview_cancelled",
]);

const DOCUMENT_TYPES = new Set<CandidateMessageType>([
  "documents_request",
  "documents_reminder",
]);

const CHANNEL_STATE_LABELS: Record<CandidateChannelState, string> = {
  not_connected: "Не подключён",
  pending_confirmation: "Ожидает подтверждения",
  allowed: "Разрешён",
  denied: "Запрещён",
  temporarily_unavailable: "Временно недоступен",
};

const CHANNEL_REASON_LABELS: Record<CandidateChannelReason, string> = {
  no_address: "У кандидата нет адреса электронной почты.",
  channel_not_configured: "Канал не настроен администратором.",
  temporary_error: "Канал временно недоступен (недавняя ошибка отправки).",
};

const STATE_TONE: Record<CandidateChannelState, StageTone> = {
  not_connected: "neutral",
  pending_confirmation: "info",
  allowed: "success",
  denied: "danger",
  temporarily_unavailable: "amber",
};

const MESSAGE_STATUS_TONE: Record<string, StageTone> = {
  queued: "neutral",
  sending: "info",
  accepted: "success",
  delivered: "teal",
  failed: "danger",
  cancelled: "neutral",
  skipped: "amber",
};

function makeIdempotencyKey(): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : "";
  if (random) return random;
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = (Math.random() * 16) | 0;
    return (char === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
}

function errorMessage(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) return caught.message;
  return caught instanceof Error ? caught.message : fallback;
}

interface CandidateCommunicationsTabProps {
  candidate: Candidate;
}

export function CandidateCommunicationsTab({ candidate }: CandidateCommunicationsTabProps) {
  const { pushToast } = useToast();

  const [channels, setChannels] = useState<CandidateChannelStatus[]>([]);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelsError, setChannelsError] = useState<string | null>(null);

  const [history, setHistory] = useState<CandidateMessage[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [interviews, setInterviews] = useState<CalendarEvent[]>([]);
  const [interviewsError, setInterviewsError] = useState(false);
  const [telegramLink, setTelegramLink] = useState<{
    deep_link: string;
    expires_at: string;
  } | null>(null);

  const loadChannels = useCallback(async () => {
    setChannelsLoading(true);
    setChannelsError(null);
    try {
      const payload = await fetchCandidateChannels(candidate.id);
      setChannels(payload.channels);
    } catch (caught) {
      setChannels([]);
      setChannelsError(errorMessage(caught, "Не удалось загрузить состояние каналов."));
    } finally {
      setChannelsLoading(false);
    }
  }, [candidate.id]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const page = await listCandidateMessages(candidate.id, HISTORY_PAGE_SIZE, 0);
      setHistory(page.items);
      setHistoryTotal(page.total);
      setHistoryOffset(0);
    } catch (caught) {
      setHistoryError(errorMessage(caught, "Не удалось загрузить историю сообщений."));
    } finally {
      setHistoryLoading(false);
    }
  }, [candidate.id]);

  const loadInterviews = useCallback(async () => {
    setInterviewsError(false);
    try {
      const page = await listEvents({
        candidate_id: candidate.id,
        type: "interview",
        limit: 100,
        sort: "starts_at",
        direction: "asc",
      });
      setInterviews(page.items);
    } catch {
      setInterviewsError(true);
    }
  }, [candidate.id]);

  useEffect(() => {
    if (candidate.is_deleted) return;
    void loadChannels();
    void loadHistory();
    void loadInterviews();
  }, [candidate.is_deleted, loadChannels, loadHistory, loadInterviews]);

  const refreshAll = useCallback(() => {
    void loadChannels();
    void loadHistory();
  }, [loadChannels, loadHistory]);

  const allowedChannels = useMemo(
    () =>
      channels
        .filter((entry) => entry.state === "allowed")
        .map((entry) => entry.channel),
    [channels]
  );

  if (candidate.is_deleted) {
    return (
      <EmptyState
        icon="inbox"
        title="Кандидат в архиве"
        description="Коммуникации недоступны для удалённой карточки."
      />
    );
  }

  const channelByKind = (kind: CandidateChannel): CandidateChannelStatus | undefined =>
    channels.find((entry) => entry.channel === kind);

  return (
    <div className="cc-tab">
      <p className="cc-note cc-note-warning">
        Отправка ставит сообщение в очередь, а «принято провайдером» не означает доставку,
        открытие или прочтение. Кандидат может в любой момент отозвать согласие по ссылке из
        письма или через Telegram.
      </p>

      {channelsLoading && <SkeletonRows rows={3} columns={2} />}
      {!channelsLoading && channelsError && <ErrorState onRetry={() => void loadChannels()} />}
      {!channelsLoading && !channelsError && (
        <section aria-label="Каналы и согласия кандидата" className="cc-section">
          <h3 className="cc-heading">Каналы и согласия</h3>
          <div className="cc-channels">
            <ChannelCard
              status={channelByKind("email")}
              kind="email"
              candidate={candidate}
              link={null}
              onLinkIssued={() => undefined}
              onLinkCleared={() => undefined}
              onChanged={() => {
                refreshAll();
                pushToast("success", "Согласие на email-канал изменено.");
              }}
            />
            <ChannelCard
              status={channelByKind("telegram")}
              kind="telegram"
              candidate={candidate}
              link={telegramLink}
              onLinkIssued={setTelegramLink}
              onLinkCleared={() => setTelegramLink(null)}
              onChanged={() => {
                refreshAll();
                pushToast("success", "Telegram-канал изменён.");
              }}
            />
          </div>
        </section>
      )}

      <SendCard
        candidate={candidate}
        allowedChannels={allowedChannels}
        interviews={interviews}
        interviewsError={interviewsError}
        onReloadInterviews={() => void loadInterviews()}
        onSent={() => {
          void loadHistory();
          pushToast(
            "success",
            "Сообщение поставлено в очередь. Доставка асинхронная; фактический результат появится в истории."
          );
        }}
      />

      <HistorySection
        candidate={candidate}
        items={history}
        total={historyTotal}
        offset={historyOffset}
        loading={historyLoading}
        error={historyError}
        onOffset={(offset) => {
          setHistoryOffset(offset);
          void (async () => {
            setHistoryLoading(true);
            setHistoryError(null);
            try {
              const page = await listCandidateMessages(candidate.id, HISTORY_PAGE_SIZE, offset);
              setHistory(page.items);
              setHistoryTotal(page.total);
            } catch (caught) {
              setHistoryError(errorMessage(caught, "Не удалось загрузить историю."));
            } finally {
              setHistoryLoading(false);
            }
          })();
        }}
        onCanceled={() => {
          void loadHistory();
          pushToast("success", "Отправка отменена.");
        }}
      />
    </div>
  );
}

/* --- Канал ----------------------------------------------------------------- */

function ChannelCard({
  status,
  kind,
  candidate,
  link,
  onLinkIssued,
  onLinkCleared,
  onChanged,
}: {
  status: CandidateChannelStatus | undefined;
  kind: CandidateChannel;
  candidate: Candidate;
  link: { deep_link: string; expires_at: string } | null;
  onLinkIssued: (link: { deep_link: string; expires_at: string }) => void;
  onLinkCleared: () => void;
  onChanged: () => void;
}) {
  const { pushToast } = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState(false);

  const state = status?.state ?? "not_connected";
  const reason = status?.reason ?? null;
  const tone = STATE_TONE[state];
  const channelName = CANDIDATE_CHANNEL_LABELS[kind];

  const run = useCallback(
    async (key: string, action: () => Promise<unknown>, toast: string) => {
      setBusy(key);
      try {
        await action();
        onChanged();
        if (toast) pushToast("success", toast);
      } catch (caught) {
        pushToast("danger", errorMessage(caught, "Не удалось выполнить действие."));
      } finally {
        setBusy(null);
      }
    },
    [onChanged, pushToast]
  );

  const requestEmailConsent = async () => {
    await requestCandidateEmailConsent(candidate.id);
    pushToast("success", "Письмо-согласие поставлено в очередь. Кандидат должен подтвердить адрес по ссылке из письма.");
  };

  const createLink = async () => {
    const result = await createCandidateTelegramLink(candidate.id);
    onLinkIssued({ deep_link: result.deep_link, expires_at: result.expires_at });
  };

  const checkConnection = async () => {
    const result = await confirmCandidateTelegram(candidate.id);
    if (result.state === "allowed") {
      onLinkCleared();
    }
    pushToast(
      result.state === "allowed" ? "success" : "info",
      result.state === "allowed" ? "Telegram-канал подключён." : result.detail
    );
  };

  const emailRequestPossible = Boolean(candidate.email) && state !== "temporarily_unavailable";
  const emailMissingNote = kind === "email" && !candidate.email;

  return (
    <div className="cc-card">
      <div className="cc-card-head">
        <span className="cc-channel-name">
          <Icon name={kind === "email" ? "mail" : "send"} size={16} />
          {channelName}
        </span>
        <Badge tone={tone}>{CHANNEL_STATE_LABELS[state]}</Badge>
      </div>

      {reason && <p className="cc-muted">{CHANNEL_REASON_LABELS[reason] ?? reason}</p>}

      {status?.recipient_masked && (
        <p className="cc-muted">
          {kind === "email" ? "Адрес" : "Чат"}: <span className="mono">{status.recipient_masked}</span>
          {status.consent_at ? ` · согласие от ${formatDateTime(status.consent_at)}` : ""}
        </p>
      )}

      {state === "pending_confirmation" && kind === "email" && (
        <p className="cc-muted">
          Ждём подтверждения по ссылке из письма
          {status?.pending_expires_at
            ? ` (ссылка действует до ${formatDateTime(status.pending_expires_at)})`
            : ""}
          . Если письмо не пришло — отправьте повторно.
        </p>
      )}

      {state === "pending_confirmation" && kind === "telegram" && (
        <>
          {link ? (
            <div className="cc-link-box">
              <p className="cc-muted">
                Отправьте кандидату ссылку (действует до {formatDateTime(link.expires_at)}), пусть
                откроет её в Telegram и нажмёт «Запустить», затем нажмите «Проверить подключение».
              </p>
              <p className="mono cc-link">{link.deep_link}</p>
            </div>
          ) : (
            <p className="cc-muted">
              Код привязки уже выдан
              {status?.pending_expires_at
                ? ` (действует до ${formatDateTime(status.pending_expires_at)})`
                : ""}
              . Если кандидат его не получил — создайте новый код.
            </p>
          )}
        </>
      )}

      {state === "allowed" && kind === "email" && (
        <p className="cc-muted">
          Сообщения на этот адрес разрешены. Кандидат может отозвать согласие по ссылке в любом
          письме.
        </p>
      )}
      {state === "allowed" && kind === "telegram" && (
        <p className="cc-muted">Сообщения в подключённый чат разрешены.</p>
      )}
      {state === "denied" && (
        <p className="cc-muted">
          Согласие отозвано, ожидающие отправки остановлены. Повторное подключение возможно только
          после нового подтверждения кандидатом.
        </p>
      )}
      {state === "temporarily_unavailable" && (
        <p className="cc-muted">Канал временно не может отправлять сообщения.</p>
      )}

      {emailMissingNote && <p className="cc-muted">У кандидата не указан email — добавьте адрес во вкладке «Сведения».</p>}

      <div className="cc-actions">
        {kind === "email" &&
          (state === "not_connected" || state === "denied") &&
          emailRequestPossible && (
            <Button
              size="sm"
              variant="secondary"
              icon="mail"
              loading={busy === "email-request"}
              disabled={busy !== null}
              onClick={() => void run("email-request", requestEmailConsent, "")}
            >
              {state === "denied" ? "Запросить согласие заново" : "Запросить согласие по email"}
            </Button>
          )}

        {kind === "email" && state === "pending_confirmation" && (
          <Button
            size="sm"
            variant="secondary"
            icon="mail"
            loading={busy === "email-request"}
            disabled={busy !== null}
            onClick={() => void run("email-request", requestEmailConsent, "")}
          >
            Отправить письмо повторно
          </Button>
        )}

        {kind === "telegram" &&
          (state === "not_connected" || state === "denied") && (
            <Button
              size="sm"
              variant="secondary"
              icon="link"
              loading={busy === "tg-link"}
              disabled={busy !== null}
              onClick={() => void run("tg-link", createLink, "Ссылка для подключения создана.")}
            >
              {state === "denied" ? "Подключить заново" : "Подключить Telegram"}
            </Button>
          )}

        {kind === "telegram" && state === "pending_confirmation" && (
          <>
            <Button
              size="sm"
              variant="secondary"
              icon="link"
              loading={busy === "tg-link"}
              disabled={busy !== null}
              onClick={() => void run("tg-link", createLink, "Новый код привязки создан.")}
            >
              Новый код
            </Button>
            <Button
              size="sm"
              variant="primary"
              icon="loader"
              loading={busy === "tg-check"}
              disabled={busy !== null}
              onClick={() => void run("tg-check", checkConnection, "")}
            >
              Проверить подключение
            </Button>
          </>
        )}

        {link && kind === "telegram" && (
          <Button
            size="sm"
            variant="secondary"
            icon="copy"
            disabled={busy !== null}
            onClick={() => {
              void navigator.clipboard?.writeText(link.deep_link).then(
                () => pushToast("success", "Ссылка скопирована."),
                () => pushToast("danger", "Не удалось скопировать ссылку.")
              );
            }}
          >
            Скопировать
          </Button>
        )}

        {state === "allowed" && (
          <Button
            size="sm"
            variant="danger"
            icon="close"
            loading={busy === "revoke"}
            disabled={busy !== null}
            onClick={() => setConfirmRevoke(true)}
          >
            Отозвать согласие
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirmRevoke}
        danger
        title={`Отозвать согласие на ${channelName.toLowerCase()}-канал?`}
        description="Ожидающие сообщения этого канала будут отменены, адрес или чат отвязаны. Повторная отправка станет возможна только после нового согласия кандидата."
        confirmLabel="Отозвать согласие"
        onCancel={() => setConfirmRevoke(false)}
        onConfirm={() => {
          setConfirmRevoke(false);
          void run("revoke", () => revokeCandidateChannel(candidate.id, kind), "");
        }}
      />
    </div>
  );
}

/* --- Ручная отправка --------------------------------------------------------- */

function SendCard({
  candidate,
  allowedChannels,
  interviews,
  interviewsError,
  onReloadInterviews,
  onSent,
}: {
  candidate: Candidate;
  allowedChannels: CandidateChannel[];
  interviews: CalendarEvent[];
  interviewsError: boolean;
  onReloadInterviews: () => void;
  onSent: () => void;
}) {
  const { pushToast } = useToast();
  const [channel, setChannel] = useState<CandidateChannel | "">("");
  const [messageType, setMessageType] = useState<CandidateManualMessageType>("documents_request");
  const [eventId, setEventId] = useState<string>("");
  const [documentsText, setDocumentsText] = useState("");
  const [confirmQuiet, setConfirmQuiet] = useState(false);
  const [preview, setPreview] = useState<{ title: string; body: string } | null>(null);
  const [quietNow, setQuietNow] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [sendBusy, setSendBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [idempotency, setIdempotency] = useState(makeIdempotencyKey);

  // Keep the selected channel consistent with the allowed set.
  useEffect(() => {
    setChannel((current) => {
      if (current && allowedChannels.includes(current)) return current;
      return allowedChannels[0] ?? "";
    });
    setPreview(null);
    setConfirmQuiet(false);
  }, [allowedChannels]);

  const typeIsInterview = INTERVIEW_TYPES.has(messageType);
  const typeIsDocuments = DOCUMENT_TYPES.has(messageType);

  const upcoming = useMemo(
    () =>
      interviews.filter(
        (event) =>
          (event.status === "scheduled" || event.status === "postponed") &&
          new Date(event.starts_at).getTime() > Date.now()
      ),
    [interviews]
  );
  const cancelledInterviews = useMemo(
    () => interviews.filter((event) => event.status === "cancelled"),
    [interviews]
  );
  const eventOptions =
    messageType === "interview_cancelled" ? cancelledInterviews : upcoming;

  useEffect(() => {
    // Reset a stale event pick once the event set or kind changes.
    setEventId((current) =>
      current && eventOptions.some((event) => event.id === current) ? current : ""
    );
    setPreview(null);
    setConfirmQuiet(false);
  }, [eventOptions, messageType]);

  const documents = useMemo(
    () =>
      documentsText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .slice(0, 30),
    [documentsText]
  );

  const payload = (): CandidateMessageSendInput => ({
    message_type: messageType,
    channel: (channel ?? "email") as CandidateChannel,
    event_id: typeIsInterview ? eventId || null : null,
    documents: typeIsDocuments ? documents : undefined,
    confirm_quiet_hours: confirmQuiet,
  });

  const showPreview = async () => {
    setFormError(null);
    if (!channel) {
      setFormError("Сначала разрешите хотя бы один канал.");
      return;
    }
    if (typeIsInterview && !eventId) {
      setFormError("Выберите собеседование — текст сообщения формируется сервером из события.");
      return;
    }
    setPreviewBusy(true);
    try {
      const result = await previewCandidateMessage(candidate.id, payload());
      setPreview({ title: result.title, body: result.body });
      setQuietNow(result.quiet_hours_now);
      setConfirmQuiet(false);
    } catch (caught) {
      setFormError(errorMessage(caught, "Не удалось сформировать предпросмотр."));
      setPreview(null);
    } finally {
      setPreviewBusy(false);
    }
  };

  const submitSend = async () => {
    if (!channel || sendBusy) return;
    setSendBusy(true);
    setFormError(null);
    try {
      const result = await sendCandidateMessage(candidate.id, {
        ...payload(),
        idempotency_key: idempotency,
      });
      setIdempotency(makeIdempotencyKey());
      setPreview(null);
      setConfirmQuiet(false);
      setDocumentsText("");
      setEventId("");
      onSent();
      if (result.duplicate) {
        pushToast("info", "Сообщение уже было поставлено ранее (повторный запрос без дубля).");
      }
    } catch (caught) {
      setFormError(errorMessage(caught, "Не удалось поставить сообщение в очередь."));
    } finally {
      setSendBusy(false);
    }
  };

  return (
    <section aria-label="Новое сообщение кандидату" className="cc-section">
      <h3 className="cc-heading">Новое сообщение</h3>

      {allowedChannels.length === 0 ? (
        <p className="cc-muted">
          Нет разрешённых каналов: разрешите email или Telegram выше, и кандидат получит письмо или
          сообщение после подтверждения согласия.
        </p>
      ) : (
        <div className="cc-form">
          <div className="cc-form-row">
            <Field label="Канал">
              {(id) => (
                <SelectInput
                  id={id}
                  value={channel}
                  onChange={(event) => {
                    setChannel(event.target.value as CandidateChannel);
                    setPreview(null);
                    setConfirmQuiet(false);
                  }}
                >
                  {allowedChannels.map((entry) => (
                    <option key={entry} value={entry}>
                      {CANDIDATE_CHANNEL_LABELS[entry]}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
            <Field label="Тип сообщения">
              {(id) => (
                <SelectInput
                  id={id}
                  value={messageType}
                  onChange={(event) => {
                    setMessageType(event.target.value as CandidateManualMessageType);
                    setPreview(null);
                    setConfirmQuiet(false);
                  }}
                >
                  {MANUAL_TYPES.map((entry) => (
                    <option key={entry} value={entry}>
                      {CANDIDATE_MESSAGE_TYPE_LABELS[entry]}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          </div>

          {typeIsInterview && (
            <Field
              label={
                messageType === "interview_cancelled"
                  ? "Отменённое собеседование"
                  : "Собеседование"
              }
              required
              hint={
                messageType === "interview_cancelled"
                  ? "Только отменённые собеседования."
                  : "Предстоящие собеседования кандидата; текст сообщения сервер возьмёт из события."
              }
              error={formError && typeIsInterview && !eventId ? formError : undefined}
            >
              {(id) => (
                <SelectInput
                  id={id}
                  value={eventId}
                  onChange={(event) => {
                    setEventId(event.target.value);
                    setPreview(null);
                    setConfirmQuiet(false);
                  }}
                >
                  <option value="">
                    {eventOptions.length === 0
                      ? "Нет подходящих собеседований"
                      : "Выберите собеседование…"}
                  </option>
                  {eventOptions.map((event) => (
                    <option key={event.id} value={event.id}>
                      {event.title} — {formatDateTime(event.starts_at)}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          )}

          {typeIsDocuments && (
            <Field
              label="Список документов"
              hint="Каждый документ с новой строки (не более 30). Список уходит в текст сообщения."
            >
              {(id, describedBy) => (
                <textarea
                  id={id}
                  aria-describedby={describedBy}
                  className="text-input cc-textarea"
                  rows={4}
                  value={documentsText}
                  onChange={(event) => {
                    setDocumentsText(event.target.value);
                    setPreview(null);
                    setConfirmQuiet(false);
                  }}
                />
              )}
            </Field>
          )}

          {interviewsError && typeIsInterview && (
            <p className="cc-muted">
              Не удалось загрузить собеседования.{" "}
              <Button size="sm" variant="ghost" onClick={onReloadInterviews}>
                Повторить
              </Button>
            </p>
          )}

          {formError && !(typeIsInterview && !eventId) && (
            <p className="cc-error" role="alert">
              {formError}
            </p>
          )}

          <div className="cc-form-actions">
            <Button
              variant="secondary"
              icon="eye"
              loading={previewBusy}
              disabled={previewBusy || sendBusy || !channel}
              onClick={() => void showPreview()}
            >
              Показать предпросмотр
            </Button>
            {preview && (
              <Button
                variant="primary"
                icon="send"
                loading={sendBusy}
                disabled={sendBusy || (quietNow && !confirmQuiet)}
                onClick={() => void submitSend()}
              >
                Поставить в очередь
              </Button>
            )}
          </div>

          {preview && (
            <div className="cc-preview">
              <p className="cc-preview-title">{preview.title}</p>
              <p className="cc-preview-body">{preview.body}</p>
              {quietNow && (
                <label className="cc-quiet">
                  <input
                    type="checkbox"
                    checked={confirmQuiet}
                    onChange={(event) => setConfirmQuiet(event.target.checked)}
                  />
                  <span>
                    Сейчас тихие часы. Отправить немедленно (сообщение срочное) — иначе оно уйдёт
                    в ближайшее разрешённое время.
                  </span>
                </label>
              )}
              <p className="cc-muted">
                Точный текст формирует сервер и он сохранится в истории без изменений. Отправка не
                означает, что кандидат прочитал сообщение.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/* --- История ---------------------------------------------------------------- */

function HistorySection({
  candidate,
  items,
  total,
  offset,
  loading,
  error,
  onOffset,
  onCanceled,
}: {
  candidate: Candidate;
  items: CandidateMessage[];
  total: number;
  offset: number;
  loading: boolean;
  error: string | null;
  onOffset: (offset: number) => void;
  onCanceled: () => void;
}) {
  const { pushToast } = useToast();
  const [cancelBusyId, setCancelBusyId] = useState<string | null>(null);

  const cancelMessage = async (message: CandidateMessage) => {
    if (cancelBusyId) return;
    setCancelBusyId(message.id);
    try {
      const result = await cancelCandidateMessage(candidate.id, message.id);
      if (!result.cancelled) {
        pushToast("info", "Сообщение уже не в очереди — отмена не требуется.");
      } else {
        onCanceled();
      }
    } catch (caught) {
      pushToast("danger", errorMessage(caught, "Не удалось отменить сообщение."));
    } finally {
      setCancelBusyId(null);
    }
  };

  return (
    <section aria-label="История сообщений кандидату" className="cc-section">
      <h3 className="cc-heading">История сообщений</h3>

      {loading && <SkeletonRows rows={3} columns={2} />}
      {!loading && error && <ErrorState onRetry={() => onOffset(offset)} />}
      {!loading && !error && items.length === 0 && (
        <EmptyState
          icon="inbox"
          title="Сообщений пока не было"
          description="Ручные отправки и автоматические письма по событиям появятся здесь в неизменяемой истории."
        />
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <ol className="cc-history">
            {items.map((message) => (
              <li key={message.id} className="cc-message">
                <div className="cc-message-head">
                  <span className="cc-message-kind">
                    <Icon
                      name={message.channel === "email" ? "mail" : "send"}
                      size={14}
                    />
                    {CANDIDATE_MESSAGE_TYPE_LABELS[message.message_type]}
                  </span>
                  <Badge tone={MESSAGE_STATUS_TONE[message.status] ?? "neutral"}>
                    {CANDIDATE_MESSAGE_STATUS_LABELS[message.status] ?? message.status}
                  </Badge>
                </div>
                <div className="cc-message-meta">
                  <span>{message.title}</span>
                  <span>
                    {formatDateTime(message.queued_at)} · {CANDIDATE_MESSAGE_SOURCE_LABELS[message.source]}
                  </span>
                </div>
                {message.recipient_masked && (
                  <p className="cc-muted">
                    Получатель: <span className="mono">{message.recipient_masked}</span>
                  </p>
                )}
                {message.status === "accepted" && message.accepted_at && (
                  <p className="cc-muted">
                    Принято провайдером: {formatDateTime(message.accepted_at)}
                    {message.provider_message_id ? ` · ID: ${message.provider_message_id}` : ""}
                  </p>
                )}
                {message.status === "failed" && message.failed_at && (
                  <p className="cc-muted">
                    Ошибка: {message.error_class ?? "неизвестно"} · {formatDateTime(message.failed_at)}
                  </p>
                )}
                {message.status === "cancelled" && message.cancelled_at && (
                  <p className="cc-muted">Отменено: {formatDateTime(message.cancelled_at)}</p>
                )}
                {message.status === "queued" && message.scheduled_at_effective && (
                  <p className="cc-muted">
                    Запланировано на {formatDateTime(message.scheduled_at_effective)}
                    {message.attempts > 0 ? ` · попыток: ${message.attempts}` : ""}
                  </p>
                )}
                <details className="cc-message-body-details">
                  <summary>Показать точный текст</summary>
                  <div className="cc-message-body">
                    <p className="cc-preview-title">{message.title}</p>
                    <p className="cc-preview-body">{message.body}</p>
                  </div>
                </details>
                {message.status === "queued" && (
                  <div className="cc-actions">
                    <Button
                      size="sm"
                      variant="secondary"
                      icon="close"
                      loading={cancelBusyId === message.id}
                      disabled={cancelBusyId !== null}
                      onClick={() => void cancelMessage(message)}
                    >
                      Отменить отправку
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ol>
          <div className="cc-pagination">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => onOffset(Math.max(0, offset - HISTORY_PAGE_SIZE))}
            >
              Назад
            </Button>
            <span>
              {total === 0 ? 0 : offset + 1}–{Math.min(offset + HISTORY_PAGE_SIZE, total)} из {total}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={offset + HISTORY_PAGE_SIZE >= total}
              onClick={() => onOffset(offset + HISTORY_PAGE_SIZE)}
            >
              Вперёд
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
