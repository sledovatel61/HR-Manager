import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cancelCandidateMessage,
  confirmCandidateTelegram,
  createCandidateTelegramInvite,
  getCandidateChannels,
  listCandidateMessages,
  listEvents,
  previewCandidateMessage,
  sendCandidateMessage,
  unlinkCandidateTelegram,
  updateCandidateChannelConsent,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Badge } from "../../design-system/components/StatusChip";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import {
  EVENT_TYPE_LABELS,
  type CalendarEvent,
  type Candidate,
  type CandidateChannelName,
  type CandidateChannelState,
  type CandidateChannels,
  type CandidateMessage,
  type CandidateMessageType,
  type CandidateMessagePreview,
} from "../../types";
import { formatDateTime } from "./format";

const MESSAGE_PAGE_SIZE = 20;
const MAX_DOCUMENT_ITEMS = 20;

const MESSAGE_TYPE_LABELS: Record<CandidateMessageType, string> = {
  interview_scheduled: "Собеседование назначено",
  interview_reminder: "Напоминание о собеседовании",
  interview_rescheduled: "Собеседование перенесено",
  interview_cancelled: "Собеседование отменено",
  document_request: "Запрос документов",
  document_reminder: "Напоминание о недостающих документах",
};

const INTERVIEW_TYPES: ReadonlySet<CandidateMessageType> = new Set([
  "interview_scheduled",
  "interview_reminder",
  "interview_rescheduled",
  "interview_cancelled",
]);

const DOCUMENT_TYPES: ReadonlySet<CandidateMessageType> = new Set([
  "document_request",
  "document_reminder",
]);

const CHANNEL_LABELS: Record<CandidateChannelName, string> = {
  email: "Электронная почта",
  telegram: "Telegram",
};

const STATE_LABELS: Record<CandidateChannelState, { label: string; tone: "neutral" | "info" | "success" | "amber" | "danger" }> = {
  not_connected: { label: "Не подключён", tone: "neutral" },
  pending: { label: "Ожидает подтверждения", tone: "info" },
  allowed: { label: "Разрешён", tone: "success" },
  forbidden: { label: "Запрещён", tone: "danger" },
  temporarily_unavailable: { label: "Временно недоступен", tone: "amber" },
};

const MESSAGE_STATUS_LABELS: Record<string, { label: string; tone: "neutral" | "info" | "success" | "amber" | "danger" }> = {
  queued: { label: "В очереди", tone: "neutral" },
  sending: { label: "Отправляется", tone: "info" },
  accepted: { label: "Принято провайдером", tone: "success" },
  failed: { label: "Не отправлено", tone: "danger" },
  cancelled: { label: "Отменено", tone: "amber" },
};

const CONSENT_SOURCE_LABELS: Record<string, string> = {
  hr_recorded: "записал HR",
  telegram_start: "сам кандидат в Telegram",
};

interface MessagesTabProps {
  candidate: Candidate;
}

/**
 * Вкладка «Сообщения»: односторонние сообщения кандидату.
 *
 * Сервер рендерит точный текст и сам выбирает адресатов по действующим
 * согласиям — клиент никогда не передаёт адрес, chat ID или текст письма.
 * `accepted` означает только техническое принятие провайдером, не «доставлено».
 */
export function MessagesTab({ candidate }: MessagesTabProps) {
  const [channels, setChannels] = useState<CandidateChannels | null>(null);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelsError, setChannelsError] = useState<string | null>(null);
  const firstLoad = useRef(true);

  const loadChannels = useCallback(async () => {
    // Only the first load shows the skeleton: refreshes keep the cards (and
    // a freshly issued Telegram invite) mounted to avoid flicker/loss.
    if (firstLoad.current) setChannelsLoading(true);
    setChannelsError(null);
    try {
      const data = await getCandidateChannels(candidate.id);
      firstLoad.current = false;
      setChannels(data);
    } catch (caught) {
      setChannelsError(
        caught instanceof ApiError ? caught.message : "Не удалось загрузить каналы."
      );
    } finally {
      setChannelsLoading(false);
    }
  }, [candidate.id]);

  useEffect(() => {
    void loadChannels();
  }, [loadChannels]);

  return (
    <div className="messages-tab">
      <section aria-label="Каналы связи с кандидатом">
        <h3 className="messages-section-title">Каналы связи</h3>
        {channelsLoading && <SkeletonRows rows={2} columns={2} />}
        {!channelsLoading && channelsError && (
          <p className="form-error" role="alert">
            {channelsError}
          </p>
        )}
        {!channelsLoading && !channelsError && channels && (
          <div className="channel-cards">
            <ChannelCard
              candidate={candidate}
              channel="email"
              status={channels.email}
              onChanged={() => void loadChannels()}
            />
            <ChannelCard
              candidate={candidate}
              channel="telegram"
              status={channels.telegram}
              onChanged={() => void loadChannels()}
            />
          </div>
        )}
      </section>

      <MessageComposer
        candidate={candidate}
        allowed={channels?.allowed_channels ?? []}
        onSent={() => void loadChannels()}
      />

      <MessageHistory candidateId={candidate.id} />
    </div>
  );
}

interface ChannelCardProps {
  candidate: Candidate;
  channel: CandidateChannelName;
  status: CandidateChannels["email"];
  onChanged: () => void;
}

function ChannelCard({ candidate, channel, status, onChanged }: ChannelCardProps) {
  const { pushToast } = useToast();
  const [busy, setBusy] = useState(false);
  const [invite, setInvite] = useState<{ deep_link: string; expires_at: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const state = STATE_LABELS[status.state];

  const withBusy = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось выполнить действие.");
    } finally {
      setBusy(false);
    }
  };

  const setConsent = (granted: boolean) =>
    withBusy(async () => {
      await updateCandidateChannelConsent(candidate.id, channel, granted);
      pushToast(
        "success",
        granted
          ? `Согласие на канал «${CHANNEL_LABELS[channel]}» записано.`
          : `Согласие на канал «${CHANNEL_LABELS[channel]}» отозвано, ожидающие отправки остановлены.`
      );
      onChanged();
    });

  const makeInvite = () =>
    withBusy(async () => {
      const result = await createCandidateTelegramInvite(candidate.id);
      setInvite(result);
      onChanged();
    });

  const confirmLink = () =>
    withBusy(async () => {
      const result = await confirmCandidateTelegram(candidate.id);
      if (result.linked) {
        pushToast("success", "Telegram кандидата подключён.");
      } else {
        pushToast("info", "Кандидат ещё не нажал «Start» в Telegram.");
      }
      setInvite(null);
      onChanged();
    });

  const unlink = () =>
    withBusy(async () => {
      await unlinkCandidateTelegram(candidate.id);
      pushToast("success", "Telegram кандидата отключён.");
      setInvite(null);
      onChanged();
    });

  return (
    <div className={`channel-card channel-card-${status.state}`}>
      <div className="channel-card-head">
        <span className="channel-card-name">{CHANNEL_LABELS[channel]}</span>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      <div className="channel-card-target">
        {status.has_target
          ? `Адресат: ${status.target_masked ?? "скрыт"}`
          : channel === "email"
            ? "В карточке не указан адрес электронной почты."
            : "Чат не подключён."}
      </div>
      {status.consent && (
        <div className="channel-card-consent">
          Согласие: {status.consent.granted ? "есть" : "нет"}
          {status.consent.granted_at &&
            ` (${CONSENT_SOURCE_LABELS[status.consent.source ?? ""] ?? "источник неизвестен"}, ${formatDateTime(status.consent.granted_at)})`}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="channel-card-actions">
        <Button
          size="sm"
          variant={status.consent?.granted ? "secondary" : "primary"}
          disabled={busy}
          onClick={() => void setConsent(!status.consent?.granted)}
        >
          {status.consent?.granted ? "Отозвать согласие" : "Записать согласие"}
        </Button>
        {channel === "telegram" && (
          <>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void makeInvite()}>
              Приглашение
            </Button>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void confirmLink()}>
              Проверить подключение
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy || !status.has_target}
              onClick={() => void unlink()}
            >
              Отключить
            </Button>
          </>
        )}
      </div>
      {channel === "telegram" && invite && (
        <div className="channel-invite">
          <p className="channel-invite-hint">
            Передайте ссылку кандидату. После того как он откроет её и нажмёт «Start», нажмите
            «Проверить подключение».
          </p>
          <code className="channel-invite-link">{invite.deep_link}</code>
          <p className="muted-text">Действует до {formatDateTime(invite.expires_at)}</p>
        </div>
      )}
      {channel === "telegram" && !invite && status.invite_active && (
        <p className="muted-text">Есть активное приглашение — создайте новое только при необходимости.</p>
      )}
    </div>
  );
}

interface MessageComposerProps {
  candidate: Candidate;
  allowed: CandidateChannelName[];
  onSent: () => void;
}

function MessageComposer({ candidate, allowed, onSent }: MessageComposerProps) {
  const { pushToast } = useToast();
  const [messageType, setMessageType] = useState<CandidateMessageType>("document_request");
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [eventId, setEventId] = useState("");
  const [documents, setDocuments] = useState("");
  const [location, setLocation] = useState("");
  const [channelChoice, setChannelChoice] = useState<"" | CandidateChannelName>("");
  const [preview, setPreview] = useState<CandidateMessagePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [sendBusy, setSendBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsEvent = INTERVIEW_TYPES.has(messageType);
  const needsDocuments = DOCUMENT_TYPES.has(messageType);

  const loadEvents = useCallback(async () => {
    try {
      const page = await listEvents({
        candidate_id: candidate.id,
        type: "interview",
        sort: "starts_at",
        direction: "desc",
        limit: 30,
      });
      setEvents(page.items);
    } catch {
      setEvents([]);
    }
  }, [candidate.id]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  const documentItems = useMemo(
    () =>
      documents
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0),
    [documents]
  );

  const buildPayload = () => ({
    message_type: messageType,
    ...(needsEvent && eventId ? { event_id: eventId } : {}),
    ...(needsDocuments ? { documents: documentItems } : {}),
    ...(location.trim() ? { location: location.trim() } : {}),
    ...(channelChoice ? { channel: channelChoice } : {}),
  });

  const validationError = useMemo(() => {
    if (needsEvent && !eventId) return "Выберите собеседование.";
    if (needsDocuments && documentItems.length === 0) return "Перечислите хотя бы один документ.";
    if (needsDocuments && documentItems.length > MAX_DOCUMENT_ITEMS)
      return `Не больше ${MAX_DOCUMENT_ITEMS} пунктов.`;
    if (channelChoice && !allowed.includes(channelChoice)) return "Выбранный канал не разрешён.";
    return null;
  }, [needsEvent, eventId, needsDocuments, documentItems, channelChoice, allowed]);

  const runPreview = async () => {
    if (validationError || previewBusy) return;
    setPreviewBusy(true);
    setError(null);
    try {
      const result = await previewCandidateMessage(candidate.id, buildPayload());
      setPreview(result);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось получить предпросмотр.");
    } finally {
      setPreviewBusy(false);
    }
  };

  const send = async () => {
    if (validationError || sendBusy) return;
    setSendBusy(true);
    setError(null);
    try {
      const result = await sendCandidateMessage(candidate.id, buildPayload());
      pushToast(
        "success",
        `Сообщение поставлено в очередь (${result.channels.map((c) => CHANNEL_LABELS[c]).join(", ")}).`
      );
      setPreview(null);
      setDocuments("");
      setLocation("");
      onSent();
      // The history below listens for the same custom event.
      window.dispatchEvent(new CustomEvent("candidate-messages-changed", { detail: candidate.id }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось поставить сообщение.");
    } finally {
      setSendBusy(false);
    }
  };

  return (
    <section aria-label="Отправка сообщения">
      <h3 className="messages-section-title">Новое сообщение</h3>
      <p className="muted-text">
        Текст формирует сервер; клиент не передаёт адрес, чат или готовый текст. «Принято
        провайдером» — технический статус, он не означает прочтение.
      </p>
      <div className="messages-compose">
        <Field label="Тип сообщения" required>
          {(id) => (
            <SelectInput
              id={id}
              value={messageType}
              onChange={(e) => {
                setMessageType(e.target.value as CandidateMessageType);
                setPreview(null);
              }}
            >
              {(Object.keys(MESSAGE_TYPE_LABELS) as CandidateMessageType[]).map((type) => (
                <option key={type} value={type}>
                  {MESSAGE_TYPE_LABELS[type]}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>

        {needsEvent && (
          <Field label="Собеседование" required>
            {(id) => (
              <SelectInput
                id={id}
                value={eventId}
                onChange={(e) => {
                  setEventId(e.target.value);
                  setPreview(null);
                }}
              >
                <option value="">— выберите —</option>
                {events.map((event) => (
                  <option key={event.id} value={event.id}>
                    {EVENT_TYPE_LABELS[event.type]} · {formatDateTime(event.starts_at)} ·{" "}
                    {event.title}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
        )}

        {needsDocuments && (
          <Field
            label="Документы"
            hint="Каждый документ с новой строки, максимум 20 пунктов"
            required
          >
            {(id, describedBy) => (
              <textarea
                id={id}
                aria-describedby={describedBy}
                className="documents-input"
                rows={4}
                value={documents}
                onChange={(e) => {
                  setDocuments(e.target.value);
                  setPreview(null);
                }}
                placeholder={"Паспорт РФ\nСНИЛС"}
              />
            )}
          </Field>
        )}

        {needsEvent && (
          <Field label="Место" hint="Необязательно: адрес или ссылка на встречу">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={location}
                maxLength={300}
                onChange={(e) => {
                  setLocation(e.target.value);
                  setPreview(null);
                }}
                placeholder="Офис, пер. Ленина 1"
              />
            )}
          </Field>
        )}

        <Field label="Канал" hint="«Все разрешённые» — по каждому каналу с действующим согласием">
          {(id, describedBy) => (
            <SelectInput
              id={id}
              aria-describedby={describedBy}
              value={channelChoice}
              onChange={(e) => {
                setChannelChoice(e.target.value as "" | CandidateChannelName);
                setPreview(null);
              }}
            >
              <option value="">Все разрешённые</option>
              <option value="email" disabled={!allowed.includes("email")}>
                Электронная почта{allowed.includes("email") ? "" : " (не разрешён)"}
              </option>
              <option value="telegram" disabled={!allowed.includes("telegram")}>
                Telegram{allowed.includes("telegram") ? "" : " (не разрешён)"}
              </option>
            </SelectInput>
          )}
        </Field>

        {validationError && (
          <p className="form-error" role="alert">
            {validationError}
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <div className="messages-compose-actions">
          <Button
            variant="secondary"
            icon="eye"
            disabled={validationError !== null}
            loading={previewBusy}
            onClick={() => void runPreview()}
          >
            Предпросмотр
          </Button>
          <Button
            variant="primary"
            icon="mail"
            disabled={validationError !== null}
            loading={sendBusy}
            onClick={() => void send()}
          >
            Отправить
          </Button>
        </div>

        {preview && (
          <div className="message-preview" aria-label="Предпросмотр сообщения">
            <div className="message-preview-title">{preview.title}</div>
            <pre className="message-preview-body">{preview.body}</pre>
            {preview.channels.length > 0 ? (
              <p className="muted-text">
                Уйдёт по каналам: {preview.channels.map((c) => CHANNEL_LABELS[c]).join(", ")}
              </p>
            ) : (
              <p className="form-error" role="alert">
                Нет разрешённых каналов — нужно согласие кандидата.
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function MessageHistory({ candidateId }: { candidateId: string }) {
  const { pushToast } = useToast();
  const [items, setItems] = useState<CandidateMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listCandidateMessages(candidateId, MESSAGE_PAGE_SIZE, offset);
      setItems(page.items);
      setTotal(page.total);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось загрузить историю.");
    } finally {
      setLoading(false);
    }
  }, [candidateId, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refresh the history right after a new message is queued by the composer.
  useEffect(() => {
    const onChange = (event: Event) => {
      if ((event as CustomEvent<string>).detail === candidateId && offset === 0) {
        void load();
      } else if ((event as CustomEvent<string>).detail === candidateId) {
        setOffset(0);
      }
    };
    window.addEventListener("candidate-messages-changed", onChange);
    return () => window.removeEventListener("candidate-messages-changed", onChange);
  }, [candidateId, offset, load]);

  const cancel = async (message: CandidateMessage) => {
    if (busyId) return;
    setBusyId(message.id);
    try {
      await cancelCandidateMessage(candidateId, message.id);
      pushToast("success", "Отправка отменена.");
      void load();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось отменить отправку."
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section aria-label="История сообщений">
      <h3 className="messages-section-title">История отправок</h3>
      {loading && <SkeletonRows rows={3} columns={2} />}
      {!loading && error && <p className="form-error" role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="muted-text">Сообщений пока не было.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <ol className="message-list">
            {items.map((message) => {
              const statusInfo =
                MESSAGE_STATUS_LABELS[message.status] ?? {
                  label: message.status,
                  tone: "neutral" as const,
                };
              return (
                <li key={message.id} className="message-item">
                  <div className="message-item-head">
                    <span className="message-item-title">{message.title}</span>
                    <Badge tone={statusInfo.tone}>{statusInfo.label}</Badge>
                  </div>
                  <div className="message-item-meta">
                    {CHANNEL_LABELS[message.channel]} · {formatDateTime(message.queued_at)} ·{" "}
                    {message.attempts > 0 ? `попыток: ${message.attempts}` : "ещё не отправлялось"}
                  </div>
                  {message.body && <pre className="message-item-body">{message.body}</pre>}
                  {message.error_class && (
                    <div className="message-item-error">Причина сбоя: {message.error_class}</div>
                  )}
                  {(message.status === "queued" || message.status === "sending") && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busyId === message.id}
                      onClick={() => void cancel(message)}
                    >
                      Отменить отправку
                    </Button>
                  )}
                </li>
              );
            })}
          </ol>
          <div className="pagination">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((current) => Math.max(0, current - MESSAGE_PAGE_SIZE))}
            >
              Назад
            </Button>
            <span className="pagination-page">
              {offset + 1}–{Math.min(offset + MESSAGE_PAGE_SIZE, total)} из {total}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={offset + MESSAGE_PAGE_SIZE >= total}
              onClick={() => setOffset((current) => current + MESSAGE_PAGE_SIZE)}
            >
              Вперёд
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
