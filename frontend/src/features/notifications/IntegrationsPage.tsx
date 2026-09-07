/** «Интеграции»: привязка Telegram, адрес email, согласия, проверка каналов.
 *
 * Every external channel here is consent-gated and honest: the backend sends
 * only after an explicit opt-in plus a verified binding, and the UI never
 * claims delivery — «принято провайдером» is not «доставлено», and neither
 * is «прочитано».
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  checkSmtpConfig,
  checkTelegramConfig,
  confirmNotificationEmail,
  confirmTelegramLink,
  createTelegramLinkCode,
  fetchAdminChannels,
  getIntegrationStatus,
  queueAdminSmtpTest,
  queueTelegramTest,
  removeNotificationEmail,
  setNotificationEmail,
  unlinkTelegram,
  updateEmailConsent,
  updateTelegramConsent,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, TextInput } from "../../design-system/components/Field";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type {
  AdminChannels,
  ChannelCheckResult,
  ChannelState,
  IntegrationStatus,
  TelegramLinkCode,
  User,
} from "../../types";
import "./notifications.css";

const STATE_LABELS: Record<ChannelState, { label: string; pill: string }> = {
  not_configured: { label: "не настроено", pill: "neutral" },
  pending: { label: "ожидает подтверждения", pill: "warn" },
  works: { label: "работает", pill: "ok" },
  temporarily_unavailable: { label: "временно недоступно", pill: "warn" },
  revoked: { label: "отозвано", pill: "bad" },
};

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return caught.message;
  return caught instanceof Error ? caught.message : "Не удалось выполнить действие.";
}

interface IntegrationsPageProps {
  user: User;
}

export function IntegrationsPage({ user }: IntegrationsPageProps) {
  const { pushToast } = useToast();
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [linkCode, setLinkCode] = useState<TelegramLinkCode | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [emailDraft, setEmailDraft] = useState("");
  const [emailToken, setEmailToken] = useState("");
  const [showEmailForm, setShowEmailForm] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [confirmEmailRemove, setConfirmEmailRemove] = useState(false);
  const [tgConsentChecked, setTgConsentChecked] = useState(false);
  const [emailConsentChecked, setEmailConsentChecked] = useState(false);
  const [adminChannels, setAdminChannels] = useState<AdminChannels | null>(null);
  const [adminBusy, setAdminBusy] = useState<string | null>(null);
  const [adminResults, setAdminResults] = useState<Record<string, ChannelCheckResult>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [own, admin] =
        user.role === "admin"
          ? await Promise.all([getIntegrationStatus(), fetchAdminChannels()])
          : [await getIntegrationStatus(), null];
      setStatus(own);
      setAdminChannels(admin);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [user.role]);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = useCallback(
    async (key: string, action: () => Promise<unknown>, reload = true) => {
      setBusy(key);
      try {
        await action();
        if (reload) {
          setStatus(await getIntegrationStatus());
        }
      } catch (caught) {
        pushToast("danger", errorMessage(caught));
      } finally {
        setBusy(null);
      }
    },
    [pushToast],
  );

  const issueLinkCode = useCallback(async () => {
    setBusy("link-code");
    try {
      const code = await createTelegramLinkCode();
      setLinkCode(code);
      setStatus(await getIntegrationStatus());
    } catch (caught) {
      pushToast("danger", errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [pushToast]);

  const confirmLink = useCallback(async () => {
    setBusy("confirm");
    try {
      const result = await confirmTelegramLink();
      if (result.linked) {
        setLinkCode(null);
        pushToast("success", "Telegram привязан. Включите получение уведомлений ниже.");
      }
      setStatus(await getIntegrationStatus());
    } catch (caught) {
      pushToast("danger", errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [pushToast]);

  const submitEmail = useCallback(async () => {
    const address = emailDraft.trim();
    if (!address) {
      pushToast("danger", "Укажите адрес электронной почты.");
      return;
    }
    await runAction("email-set", async () => {
      const result = await setNotificationEmail(address);
      setEmailDraft("");
      setShowEmailForm(false);
      setEmailToken("");
      pushToast(
        "success",
        result.verification_queued
          ? `Письмо с кодом отправлено на ${result.pending_email_masked}.`
          : `Адрес ${result.pending_email_masked} принят, письмо в очереди.`,
      );
    });
  }, [emailDraft, pushToast, runAction]);

  const submitEmailToken = useCallback(async () => {
    const token = emailToken.trim();
    if (!token) {
      pushToast("danger", "Вставьте код из письма.");
      return;
    }
    await runAction("email-confirm", async () => {
      const result = await confirmNotificationEmail(token);
      setEmailToken("");
      pushToast(
        result.verified ? "success" : "danger",
        result.verified
          ? `Адрес ${result.address_masked} подтверждён. Включите получение писем ниже.`
          : "Код не подошёл.",
      );
    });
  }, [emailToken, pushToast, runAction]);

  const runAdmin = useCallback(
    async (key: string, action: () => Promise<ChannelCheckResult | { detail: string }>) => {
      setAdminBusy(key);
      try {
        const result = await action();
        if ("ok" in result) {
          setAdminResults((current) => ({ ...current, [key]: result }));
        }
        pushToast("success", result.detail);
      } catch (caught) {
        pushToast("danger", errorMessage(caught));
      } finally {
        setAdminBusy(null);
      }
    },
    [pushToast],
  );

  if (loading) return <SkeletonRows rows={3} columns={2} />;
  if (loadError || !status) return <ErrorState onRetry={() => void load()} />;

  const telegram = status.telegram;
  const email = status.email;
  const telegramState = STATE_LABELS[telegram.state];
  const emailState = STATE_LABELS[email.state];

  return (
    <div className="notif-page" aria-live="polite">
      <div className="pref-card">
        <h3 className="notif-card-title">Telegram</h3>
        <div className="channel-row">
          <span>Состояние канала</span>
          <span className={`status-pill ${telegramState.pill}`}>{telegramState.label}</span>
        </div>
        {!telegram.configured && (
          <p className="notif-card-body">
            Бот не настроен на сервере. Привязка станет доступна после настройки администратором —
            секреты хранятся только в переменных окружения сервера.
          </p>
        )}
        {telegram.configured && !telegram.linked && (
          <>
            <p className="notif-card-body">
              Привязка одноразовая: ссылка действует ограниченное время и сгорает после
              использования. Ваш chat id увидит только сервер — в интерфейсе он маскируется.
            </p>
            {!linkCode && (
              <Button
                variant="primary"
                loading={busy === "link-code"}
                onClick={() => void issueLinkCode()}
              >
                Получить ссылку для привязки
              </Button>
            )}
            {linkCode && (
              <div className="link-code-block">
                <p className="notif-card-body">
                  Шаг 1 — откройте ссылку и нажмите <b>Start</b> у бота (действует до{" "}
                  {formatDateTime(linkCode.expires_at)}):
                </p>
                <a
                  className="deep-link"
                  href={linkCode.deep_link}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  Открыть бота в Telegram
                </a>
                <p className="notif-card-body">Шаг 2 — вернитесь сюда и подтвердите привязку:</p>
                <div className="button-row">
                  <Button
                    variant="primary"
                    loading={busy === "confirm"}
                    onClick={() => void confirmLink()}
                  >
                    Я нажал Start — подтвердить
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy !== null}
                    onClick={() => void issueLinkCode()}
                  >
                    Выпустить новую ссылку
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
        {telegram.linked && (
          <>
            <div className="channel-row">
              <span>Привязанный чат</span>
              <span className="mono">{telegram.masked_chat_id ?? "—"}</span>
            </div>
            <div className="channel-row">
              <span>Привязан</span>
              <span>{formatDateTime(telegram.linked_at)}</span>
            </div>
            <p className="notif-card-body">
              {telegram.opt_in
                ? `Уведомления в Telegram включены${telegram.consent_at ? ` (согласие от ${formatDateTime(telegram.consent_at)})` : ""}. Отключить можно в любой момент — привязка сохранится.`
                : "Привязка есть, но уведомления выключены: сервер ничего не отправляет без вашего согласия."}
            </p>
            {!telegram.opt_in && (
              <label className="consent-check">
                <input
                  type="checkbox"
                  checked={tgConsentChecked}
                  onChange={(event) => setTgConsentChecked(event.target.checked)}
                />
                <span>
                  Я даю согласие на получение уведомлений в Telegram на привязанный чат. Сервер
                  начнёт отправку только после явного согласия.
                </span>
              </label>
            )}
            <div className="button-row">
              <Button
                variant={telegram.opt_in ? "secondary" : "primary"}
                loading={busy === "tg-consent"}
                disabled={!telegram.opt_in && !tgConsentChecked}
                onClick={() =>
                  telegram.opt_in
                    ? void runAction("tg-consent", () => updateTelegramConsent(false, false))
                    : void runAction("tg-consent", async () => {
                        const result = await updateTelegramConsent(true, true);
                        setTgConsentChecked(false);
                        return result;
                      })
                }
              >
                {telegram.opt_in ? "Не получать в Telegram" : "Получать в Telegram"}
              </Button>
              <Button
                variant="secondary"
                loading={busy === "tg-test"}
                onClick={() =>
                  void runAction("tg-test", async () => {
                    const result = await queueTelegramTest();
                    pushToast(
                      "success",
                      `Тест поставлен в очередь (статус: ${result.status}). Доставка асинхронная — честный результат покажет сервер.`,
                    );
                  })
                }
              >
                Отправить тест
              </Button>
              <Button variant="danger" disabled={busy !== null} onClick={() => setConfirmUnlink(true)}>
                Отвязать
              </Button>
            </div>
          </>
        )}
      </div>

      <div className="pref-card">
        <h3 className="notif-card-title">Email</h3>
        <div className="channel-row">
          <span>Состояние канала</span>
          <span className={`status-pill ${emailState.pill}`}>{emailState.label}</span>
        </div>
        {!email.configured && (
          <p className="notif-card-body">
            Почтовый сервер не настроен. Адрес можно будет указать после настройки
            администратором.
          </p>
        )}
        {email.configured && !email.verified && !email.pending_confirmation && !showEmailForm && (
          <>
            <p className="notif-card-body">
              Укажите адрес — на него придёт письмо с одноразовым кодом. Уведомления включаются
              отдельно, только с вашего согласия.
            </p>
            <Button variant="primary" onClick={() => setShowEmailForm(true)}>
              Указать адрес
            </Button>
          </>
        )}
        {email.configured && showEmailForm && (
          <div className="email-form">
            <Field label="Адрес для уведомлений">
              {(id) => (
                <TextInput
                  id={id}
                  type="email"
                  autoComplete="email"
                  placeholder="name@example.com"
                  value={emailDraft}
                  onChange={(event) => setEmailDraft(event.target.value)}
                />
              )}
            </Field>
            <div className="button-row">
              <Button variant="primary" loading={busy === "email-set"} onClick={() => void submitEmail()}>
                Отправить код
              </Button>
              <Button variant="secondary" disabled={busy !== null} onClick={() => setShowEmailForm(false)}>
                Отмена
              </Button>
            </div>
          </div>
        )}
        {email.pending_confirmation && !showEmailForm && (
          <>
            <p className="notif-card-body">
              Письмо отправлено на {email.pending_email_masked ?? "указанный адрес"}. Вставьте код
              из письма (попытки ограничены):
            </p>
            <div className="email-form">
              <Field label="Код из письма">
                {(id) => (
                  <TextInput
                    id={id}
                    autoComplete="one-time-code"
                    value={emailToken}
                    onChange={(event) => setEmailToken(event.target.value)}
                  />
                )}
              </Field>
              <div className="button-row">
                <Button
                  variant="primary"
                  loading={busy === "email-confirm"}
                  onClick={() => void submitEmailToken()}
                >
                  Подтвердить адрес
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() => setShowEmailForm(true)}
                >
                  Другой адрес
                </Button>
              </div>
            </div>
          </>
        )}
        {email.verified && (
          <>
            <div className="channel-row">
              <span>Подтверждённый адрес</span>
              <span className="mono">{email.address_masked ?? "—"}</span>
            </div>
            <p className="notif-card-body">
              {email.opt_in
                ? `Письма включены${email.consent_at ? ` (согласие от ${formatDateTime(email.consent_at)})` : ""}.`
                : "Адрес подтверждён, но письма выключены: сервер ничего не отправляет без вашего согласия."}
            </p>
            {!email.opt_in && (
              <label className="consent-check">
                <input
                  type="checkbox"
                  checked={emailConsentChecked}
                  onChange={(event) => setEmailConsentChecked(event.target.checked)}
                />
                <span>
                  Я даю согласие на получение уведомлений по email на подтверждённый адрес. Сервер
                  начнёт отправку только после явного согласия.
                </span>
              </label>
            )}
            <div className="button-row">
              <Button
                variant={email.opt_in ? "secondary" : "primary"}
                loading={busy === "email-consent"}
                disabled={!email.opt_in && !emailConsentChecked}
                onClick={() =>
                  email.opt_in
                    ? void runAction("email-consent", () => updateEmailConsent(false, false))
                    : void runAction("email-consent", async () => {
                        const result = await updateEmailConsent(true, true);
                        setEmailConsentChecked(false);
                        return result;
                      })
                }
              >
                {email.opt_in ? "Не получать письма" : "Получать письма"}
              </Button>
              <Button
                variant="danger"
                disabled={busy !== null}
                onClick={() => setConfirmEmailRemove(true)}
              >
                Удалить адрес
              </Button>
            </div>
          </>
        )}
      </div>

      {user.role === "admin" && (
        <div className="pref-card">
          <h3 className="notif-card-title">Проверка каналов (администратор)</h3>
          {!adminChannels && (
            <p className="notif-card-body">Глобальная конфигурация недоступна.</p>
          )}
          {adminChannels && (
            <>
              <div className="channel-row">
                <span>Telegram-бот</span>
                <span className="mono">
                  {adminChannels.telegram.configured
                    ? (adminChannels.telegram.bot_username ?? "настроен")
                    : "не настроен"}
                </span>
              </div>
              <div className="channel-row">
                <span>SMTP</span>
                <span className="mono">
                  {adminChannels.smtp.configured
                    ? `${adminChannels.smtp.host ?? "?"}:${adminChannels.smtp.port ?? "?"} (${adminChannels.smtp.encryption ?? "?"})`
                    : "не настроен"}
                </span>
              </div>
              <p className="notif-card-body">
                Проверки выполняют живой запрос без отправки сообщений; секреты и токены никогда не
                покидают сервер.
              </p>
              <div className="button-row">
                <Button
                  variant="secondary"
                  loading={adminBusy === "tg-check"}
                  onClick={() => void runAdmin("tg-check", checkTelegramConfig)}
                >
                  Проверить Telegram
                </Button>
                <Button
                  variant="secondary"
                  loading={adminBusy === "smtp-check"}
                  onClick={() => void runAdmin("smtp-check", checkSmtpConfig)}
                >
                  Проверить SMTP
                </Button>
                <Button
                  variant="secondary"
                  loading={adminBusy === "smtp-test"}
                  onClick={() =>
                    void runAdmin("smtp-test", async () => {
                      const result = await queueAdminSmtpTest();
                      return { detail: `Тест поставлен в очередь (статус: ${result.status}).` };
                    })
                  }
                >
                  Отправить тест себе
                </Button>
              </div>
              {Object.entries(adminResults).map(([key, result]) => (
                <p key={key} className="notif-card-body">
                  {key === "tg-check" ? "Telegram: " : "SMTP: "}
                  {result.ok ? "OK" : "Ошибка"} — {result.detail}
                </p>
              ))}
            </>
          )}
        </div>
      )}

      <div className="pref-card">
        <h3 className="notif-card-title">Честно о доставке</h3>
        <p className="notif-card-body">
          «Принято провайдером» означает лишь, что Telegram или почтовый сервер взяли сообщение в
          работу. Это не «доставлено» и тем более не «прочитано»: интерфейс никогда не показывает
          прочтение, которого не было.
        </p>
      </div>

      <ConfirmDialog
        open={confirmUnlink}
        onCancel={() => setConfirmUnlink(false)}
        onConfirm={() => {
          setConfirmUnlink(false);
          void runAction("tg-unlink", async () => {
            await unlinkTelegram();
            setLinkCode(null);
            pushToast("success", "Telegram отвязан: отправка остановлена.");
          });
        }}
        title="Отвязать Telegram?"
        description="Привязка и согласие будут удалены, отправка в ваш чат остановится. Привязать заново можно в любой момент."
        confirmLabel="Отвязать"
        danger
      />
      <ConfirmDialog
        open={confirmEmailRemove}
        onCancel={() => setConfirmEmailRemove(false)}
        onConfirm={() => {
          setConfirmEmailRemove(false);
          void runAction("email-remove", async () => {
            await removeNotificationEmail();
            pushToast("success", "Адрес удалён: письма остановлены.");
          });
        }}
        title="Удалить адрес?"
        description="Адрес, подтверждение и согласие будут удалены, письма остановятся. Указать адрес заново можно в любой момент."
        confirmLabel="Удалить"
        danger
      />
    </div>
  );
}
