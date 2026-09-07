/** «Настройки уведомлений»: timezone, quiet hours, workdays, types,
 * Telegram Bot linking, and universal email notifications (Phase 9).
 */

import { useCallback, useEffect, useState } from "react";
import {
  confirmTelegramLink,
  getPreferences,
  initiateTelegramLink,
  listTimezones,
  savePreferences,
  unlinkTelegram,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type { NotificationPreferences, TelegramLinkInitiateOut } from "../../types";
import "./notifications.css";

const TYPE_LABELS: Record<string, string> = {
  event_assigned: "Назначено событие",
  event_approaching: "Событие приближается",
  event_overdue: "Событие просрочено",
  event_rescheduled: "Событие перенесено",
  event_cancelled: "Событие отменено",
  candidate_transferred: "Передан кандидат",
  reminder_due: "Наступило напоминание",
  reminder_overdue: "Напоминание просрочено",
  system_alert: "Системные сообщения",
};

const WEEKDAYS: Array<[number, string]> = [
  [1, "Пн"],
  [2, "Вт"],
  [3, "Ср"],
  [4, "Чт"],
  [5, "Пт"],
  [6, "Сб"],
  [7, "Вс"],
];

function formatDate(val: string | null | undefined): string {
  if (!val) return "";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(val));
  } catch {
    return val;
  }
}

export function PreferencesPage() {
  const { pushToast } = useToast();
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [timezones, setTimezones] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);

  // Telegram link modal state
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [linkData, setLinkData] = useState<TelegramLinkInitiateOut | null>(null);
  const [initiatingLink, setInitiatingLink] = useState(false);
  const [manualChatId, setManualChatId] = useState("");
  const [confirmingLink, setConfirmingLink] = useState(false);

  // Telegram unlink dialog state
  const [unlinkDialogOpen, setUnlinkDialogOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [prefs, zones] = await Promise.all([getPreferences(), listTimezones()]);
      setPreferences(prefs);
      setTimezones(zones.timezones);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleWorkday = useCallback((day: number) => {
    setPreferences((current) => {
      if (!current) return current;
      const workdays = current.workdays.includes(day)
        ? current.workdays.filter((item) => item !== day)
        : [...current.workdays, day].sort((a, b) => a - b);
      return { ...current, workdays };
    });
  }, []);

  const toggleType = useCallback((type: string) => {
    setPreferences((current) => {
      if (!current) return current;
      const enabledTypes = current.enabled_types.includes(type)
        ? current.enabled_types.filter((item) => item !== type)
        : [...current.enabled_types, type];
      return { ...current, enabled_types: enabledTypes };
    });
  }, []);

  const save = useCallback(async () => {
    if (!preferences) return;
    setSaving(true);
    try {
      const saved = await savePreferences({
        timezone: preferences.timezone,
        quiet_hours_start: preferences.quiet_hours_start,
        quiet_hours_end: preferences.quiet_hours_end,
        workdays: preferences.workdays,
        enabled_types: preferences.enabled_types,
        enabled_channels: preferences.enabled_channels,
        email_address: preferences.email_address,
        email_opt_in: preferences.email_opt_in,
        telegram_opt_in: preferences.telegram_opt_in,
      });
      setPreferences(saved);
      pushToast("success", "Настройки сохранены.");
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Не удалось сохранить.");
    } finally {
      setSaving(false);
    }
  }, [preferences, pushToast]);

  const handleStartTelegramLink = useCallback(async () => {
    setInitiatingLink(true);
    try {
      const res = await initiateTelegramLink();
      setLinkData(res);
      setManualChatId("");
      setLinkModalOpen(true);
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof Error ? caught.message : "Не удалось начать привязку Telegram."
      );
    } finally {
      setInitiatingLink(false);
    }
  }, [pushToast]);

  const handleConfirmTelegramLink = useCallback(async () => {
    if (!linkData || !manualChatId.trim()) {
      pushToast("info", "Укажите числовой Chat ID для ручного подтверждения.");
      return;
    }
    const chatIdNum = parseInt(manualChatId.trim(), 10);
    if (isNaN(chatIdNum) || chatIdNum <= 0) {
      pushToast("danger", "Некорректный числовой Chat ID.");
      return;
    }
    setConfirmingLink(true);
    try {
      await confirmTelegramLink({
        token: linkData.token,
        chat_id: chatIdNum,
      });
      pushToast("success", "Telegram успешно привязан!");
      setLinkModalOpen(false);
      setLinkData(null);
      await load();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof Error ? caught.message : "Ошибка подтверждения привязки."
      );
    } finally {
      setConfirmingLink(false);
    }
  }, [linkData, manualChatId, load, pushToast]);

  const handleUnlinkTelegram = useCallback(async () => {
    try {
      await unlinkTelegram();
      pushToast("success", "Telegram успешно отвязан.");
      setUnlinkDialogOpen(false);
      await load();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof Error ? caught.message : "Не удалось отвязать Telegram."
      );
    }
  }, [load, pushToast]);

  const copyDeepLink = useCallback(() => {
    if (linkData?.deep_link) {
      void navigator.clipboard.writeText(linkData.deep_link);
      pushToast("info", "Ссылка скопирована в буфер обмена.");
    }
  }, [linkData, pushToast]);

  if (loading) return <SkeletonRows rows={4} columns={3} />;
  if (error || !preferences) return <ErrorState onRetry={() => void load()} />;

  const isTelegramLinked = Boolean(preferences.telegram_chat_id);
  const isEmailActive = Boolean(preferences.email_address && preferences.email_opt_in);

  return (
    <div className="notif-page" aria-live="polite">
      {/* 1. Timezone & Quiet Hours */}
      <div className="pref-card">
        <h3 className="notif-card-title">Время и тихие часы</h3>
        <div className="pref-grid">
          <Field label="Часовая зона (IANA)">
            {(id) => (
              <SelectInput
                id={id}
                value={preferences.timezone}
                onChange={(event) =>
                  setPreferences({ ...preferences, timezone: event.target.value })
                }
              >
                {timezones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field
            label="Тихие часы: начало (местное время)"
            hint="Автоматические сообщения в тихий период переносятся на первое разрешённое время."
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="time"
                value={preferences.quiet_hours_start}
                onChange={(event) =>
                  setPreferences({ ...preferences, quiet_hours_start: event.target.value })
                }
              />
            )}
          </Field>
          <Field
            label="Тихие часы: конец (местное время)"
            hint="Например, 21:00–08:00 — интервал через полночь."
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="time"
                value={preferences.quiet_hours_end}
                onChange={(event) =>
                  setPreferences({ ...preferences, quiet_hours_end: event.target.value })
                }
              />
            )}
          </Field>
        </div>
      </div>

      {/* 2. Workdays */}
      <div className="pref-card">
        <h3 className="notif-card-title">Рабочие дни</h3>
        <p className="notif-card-body">
          Напоминания «по рабочим дням» и перенос из тихих часов учитывают этот набор.
        </p>
        <div className="workday-toggles">
          {WEEKDAYS.map(([day, label]) => (
            <button
              key={day}
              type="button"
              className="toggle-chip"
              aria-pressed={preferences.workdays.includes(day)}
              onClick={() => toggleWorkday(day)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 3. Notification Types */}
      <div className="pref-card">
        <h3 className="notif-card-title">Какие уведомления получать</h3>
        <div className="type-toggles">
          {Object.entries(TYPE_LABELS).map(([type, label]) => (
            <button
              key={type}
              type="button"
              className="toggle-chip"
              aria-pressed={preferences.enabled_types.includes(type)}
              onClick={() => toggleType(type)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 4. Communication Channels and Consent */}
      <div className="pref-card">
        <h3 className="notif-card-title">Каналы доставки и согласие</h3>
        <p className="notif-card-body">
          Доставка осуществляется только по активным каналам с явно подтверждённым согласием.
        </p>

        {/* In-App */}
        <div className="integration-channel-box">
          <div className="integration-header">
            <div className="integration-title-wrap">
              <h4 className="integration-title">Уведомления в интерфейсе</h4>
            </div>
            <span className="status-pill ok">работает</span>
          </div>
          <div className="integration-content">
            <span>Центр уведомлений в веб-интерфейсе системы. Включен по умолчанию.</span>
          </div>
        </div>

        {/* Telegram */}
        <div className="integration-channel-box">
          <div className="integration-header">
            <div className="integration-title-wrap">
              <h4 className="integration-title">Telegram</h4>
            </div>
            {isTelegramLinked ? (
              preferences.telegram_opt_in ? (
                <span className="status-pill ok">подключен</span>
              ) : (
                <span className="status-pill warn">отозван</span>
              )
            ) : (
              <span className="status-pill neutral">не привязан</span>
            )}
          </div>

          <div className="integration-content">
            {isTelegramLinked ? (
              <>
                <div>
                  {preferences.telegram_username ? (
                    <span>Привязанный аккаунт: <strong>@{preferences.telegram_username}</strong></span>
                  ) : (
                    <span>Telegram привязан (ID: {preferences.telegram_chat_id})</span>
                  )}
                </div>
                {preferences.telegram_consent_at && (
                  <div className="consent-meta-info">
                    Согласие выдано: {formatDate(preferences.telegram_consent_at)}
                  </div>
                )}
                <label className="consent-checkbox-row">
                  <input
                    type="checkbox"
                    data-testid="telegram-optin-toggle"
                    checked={Boolean(preferences.telegram_opt_in)}
                    onChange={(e) =>
                      setPreferences({ ...preferences, telegram_opt_in: e.target.checked })
                    }
                  />
                  <span>Получать уведомления в Telegram</span>
                </label>
                <div>
                  <Button
                    variant="ghost"
                    size="sm"
                    data-testid="unlink-telegram-btn"
                    onClick={() => setUnlinkDialogOpen(true)}
                  >
                    Отвязать Telegram
                  </Button>
                </div>
              </>
            ) : (
              <>
                <p className="notif-card-body">
                  Привяжите ваш Telegram-аккаунт для мгновенного получения уведомлений о событиях и
                  напоминаниях.
                </p>
                <div>
                  <Button
                    variant="secondary"
                    data-testid="link-telegram-btn"
                    loading={initiatingLink}
                    onClick={() => void handleStartTelegramLink()}
                  >
                    Привязать Telegram
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Email */}
        <div className="integration-channel-box">
          <div className="integration-header">
            <div className="integration-title-wrap">
              <h4 className="integration-title">Электронная почта (Email)</h4>
            </div>
            {isEmailActive ? (
              <span className="status-pill ok">подключен</span>
            ) : preferences.email_address ? (
              <span className="status-pill warn">отозван</span>
            ) : (
              <span className="status-pill neutral">не настроен</span>
            )}
          </div>

          <div className="integration-content">
            <Field label="Email для уведомлений" hint="Рабочий адрес электронной почты.">
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  data-testid="email-address-input"
                  aria-describedby={describedBy}
                  type="email"
                  placeholder="name@company.com"
                  value={preferences.email_address ?? ""}
                  onChange={(event) =>
                    setPreferences({ ...preferences, email_address: event.target.value })
                  }
                />
              )}
            </Field>

            <label className="consent-checkbox-row">
              <input
                type="checkbox"
                data-testid="email-consent-toggle"
                checked={Boolean(preferences.email_opt_in)}
                onChange={(e) =>
                  setPreferences({ ...preferences, email_opt_in: e.target.checked })
                }
              />
              <span>
                Даю согласие на получение рабочих уведомлений на указанный адрес электронной почты
              </span>
            </label>

            {preferences.email_consent_at && preferences.email_opt_in && (
              <div className="consent-meta-info">
                Согласие предоставлено: {formatDate(preferences.email_consent_at)}
              </div>
            )}
          </div>
        </div>
      </div>

      <div>
        <Button variant="primary" loading={saving} onClick={() => void save()}>
          Сохранить настройки
        </Button>
      </div>

      {/* Telegram Link Modal */}
      <Modal
        open={linkModalOpen}
        title="Привязка Telegram"
        onClose={() => setLinkModalOpen(false)}
        footer={
          <div style={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
            <Button variant="ghost" onClick={() => setLinkModalOpen(false)}>
              Отмена
            </Button>
            <Button
              variant="primary"
              loading={confirmingLink}
              onClick={() => void handleConfirmTelegramLink()}
            >
              Подтвердить привязку
            </Button>
          </div>
        }
      >
        {linkData && (
          <div className="link-token-card">
            <p style={{ margin: 0 }}>
              <strong>Способ 1 (быстрый):</strong> Нажмите кнопку ниже или откройте ссылку в Telegram
              и нажмите <em>Запустить / Start</em>:
            </p>

            {linkData.deep_link && (
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <a
                  href={linkData.deep_link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="button secondary"
                  style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}
                >
                  Открыть в Telegram
                </a>
                <Button variant="ghost" size="sm" onClick={copyDeepLink}>
                  Копировать ссылку
                </Button>
              </div>
            )}

            <div className="deep-link-box">
              <span>{linkData.deep_link ?? `Команда: /start ${linkData.token}`}</span>
            </div>

            <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "8px 0" }} />

            <p style={{ margin: 0 }}>
              <strong>Способ 2 (ручной ввод):</strong> Отправьте боту команду{" "}
              <code>/start {linkData.token}</code> и введите ваш Telegram Chat ID ниже для проверки:
            </p>

            <Field label="Ваш числовой Chat ID">
              {(id) => (
                <TextInput
                  id={id}
                  type="text"
                  placeholder="Например: 123456789"
                  value={manualChatId}
                  onChange={(e) => setManualChatId(e.target.value)}
                />
              )}
            </Field>

            <div className="consent-meta-info">
              Код привязки одноразовый и действует до: {formatDate(linkData.expires_at)}.
            </div>
          </div>
        )}
      </Modal>

      {/* Telegram Unlink Confirmation */}
      <ConfirmDialog
        open={unlinkDialogOpen}
        title="Отвязать Telegram?"
        description="Вы уверены, что хотите отвязать Telegram от вашей учётной записи? Согласие на получение уведомлений будет отозвано, и отправка сообщений в Telegram прекратится."
        confirmLabel="Да, отвязать"
        danger
        onCancel={() => setUnlinkDialogOpen(false)}
        onConfirm={() => void handleUnlinkTelegram()}
      />
    </div>
  );
}
