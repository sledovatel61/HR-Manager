/** Admin: notification queue diagnostics, pilot setup, and integration channel probes (Phase 9).
 *
 * Diagnostics are counters and statuses only (the backend never returns
 * PII here). The pilot block creates the single pilot account with an
 * explicit full-access grant. Admin probes test Telegram and SMTP
 * configurations and test message dispatch without leaking tokens or passwords.
 */

import { useCallback, useEffect, useState } from "react";
import {
  adminTestSend,
  adminTestSmtpConnection,
  adminTestTelegramConnection,
  createPilot,
  fetchIntegrationsStatus,
  fetchQueueDiagnostics,
  fetchSetupState,
  listAccessGrants,
  revokePilotAccess,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextAreaInput, TextInput } from "../../design-system/components/Field";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type {
  AccessGrant,
  AdminSmtpTestConnectionOut,
  AdminTelegramTestConnectionOut,
  AdminTestSendOut,
  IntegrationStatusResponse,
  QueueDiagnostics,
  SetupState,
} from "../../types";
import "./notifications.css";

const STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  sending: "Отправляется",
  accepted: "Принято провайдером",
  delivered: "Доставлено",
  failed: "Ошибка",
  cancelled: "Отменено",
  skipped: "Пропущено",
};

function workerPill(worker: QueueDiagnostics["worker"]) {
  if (worker.alive) {
    return (
      <span className="status-pill ok">
        работает
        {worker.processed_total !== undefined && ` · обработано ${worker.processed_total}`}
      </span>
    );
  }
  return (
    <span className="status-pill warn">
      требуется действие: worker не запущен или не отвечает
    </span>
  );
}

export function AdminQueuePage() {
  const { pushToast } = useToast();
  const [diagnostics, setDiagnostics] = useState<QueueDiagnostics | null>(null);
  const [setup, setSetup] = useState<SetupState | null>(null);
  const [integrationsStatus, setIntegrationsStatus] = useState<IntegrationStatusResponse | null>(null);
  const [grants, setGrants] = useState<AccessGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Pilot form
  const [pilotForm, setPilotForm] = useState({ username: "", password: "", full_name: "" });
  const [savingPilot, setSavingPilot] = useState(false);

  // Probe testing state
  const [testingTelegram, setTestingTelegram] = useState(false);
  const [telegramProbeResult, setTelegramProbeResult] = useState<AdminTelegramTestConnectionOut | null>(null);

  const [testingSmtp, setTestingSmtp] = useState(false);
  const [smtpProbeResult, setSmtpProbeResult] = useState<AdminSmtpTestConnectionOut | null>(null);

  // Test send state
  const [testSendChannel, setTestSendChannel] = useState<"telegram" | "email">("telegram");
  const [testSendRecipient, setTestSendRecipient] = useState("");
  const [testSendSubject, setTestSendSubject] = useState("");
  const [testSendBody, setTestSendBody] = useState("");
  const [sendingTest, setSendingTest] = useState(false);
  const [testSendResult, setTestSendResult] = useState<AdminTestSendOut | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [diag, state, grantList, integ] = await Promise.all([
        fetchQueueDiagnostics(),
        fetchSetupState(),
        listAccessGrants(),
        fetchIntegrationsStatus(),
      ]);
      setDiagnostics(diag);
      setSetup(state);
      setGrants(grantList.items);
      setIntegrationsStatus(integ);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleTestTelegram = useCallback(async () => {
    setTestingTelegram(true);
    setTelegramProbeResult(null);
    try {
      const res = await adminTestTelegramConnection();
      setTelegramProbeResult(res);
      if (res.ok) {
        pushToast("success", `Бот доступен: @${res.bot_username}`);
      } else {
        pushToast("danger", res.error ?? "Ошибка соединения с Telegram Bot API.");
      }
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Ошибка проверки Telegram.");
    } finally {
      setTestingTelegram(false);
    }
  }, [pushToast]);

  const handleTestSmtp = useCallback(async () => {
    setTestingSmtp(true);
    setSmtpProbeResult(null);
    try {
      const res = await adminTestSmtpConnection();
      setSmtpProbeResult(res);
      if (res.ok) {
        pushToast("success", `SMTP сервер доступен: ${res.host}:${res.port}`);
      } else {
        pushToast("danger", res.error ?? "Ошибка соединения с SMTP.");
      }
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Ошибка проверки SMTP.");
    } finally {
      setTestingSmtp(false);
    }
  }, [pushToast]);

  const handleTestSend = useCallback(async () => {
    setSendingTest(true);
    setTestSendResult(null);
    try {
      const res = await adminTestSend({
        channel: testSendChannel,
        recipient: testSendRecipient.trim() || undefined,
        subject: testSendSubject.trim() || undefined,
        body: testSendBody.trim() || undefined,
      });
      setTestSendResult(res);
      pushToast("success", res.message);
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Ошибка тестовой отправки.");
    } finally {
      setSendingTest(false);
    }
  }, [testSendChannel, testSendRecipient, testSendSubject, testSendBody, pushToast]);

  const createPilotAccount = useCallback(async () => {
    if (!pilotForm.username.trim() || pilotForm.password.length < 12) {
      pushToast("info", "Задайте имя и пароль пилота (не короче 12 символов).");
      return;
    }
    setSavingPilot(true);
    try {
      await createPilot({
        username: pilotForm.username.trim(),
        password: pilotForm.password,
        full_name: pilotForm.full_name.trim(),
      });
      pushToast("success", "Пилотный пользователь создан с полным доступом.");
      setPilotForm({ username: "", password: "", full_name: "" });
      await load();
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Не удалось создать пилота.");
    } finally {
      setSavingPilot(false);
    }
  }, [load, pilotForm, pushToast]);

  const revoke = useCallback(
    async (userId: string) => {
      try {
        await revokePilotAccess(userId, "отозвано администратором");
        pushToast("success", "Доступ пилота отозван.");
        await load();
      } catch {
        pushToast("danger", "Не удалось отозвать доступ.");
      }
    },
    [load, pushToast],
  );

  if (loading) return <SkeletonRows rows={5} columns={4} />;
  if (error || !diagnostics || !setup) return <ErrorState onRetry={() => void load()} />;

  const counts = diagnostics.counts;

  return (
    <div className="notif-page" aria-live="polite">
      {/* 1. Queue Status */}
      <div className="pref-card">
        <h3 className="notif-card-title">Состояние очереди и worker</h3>
        <div className="queue-cards">
          {Object.entries(STATUS_LABELS).map(([status, label]) => (
            <div className="queue-card" key={status}>
              <div className="queue-card-value">{counts[status] ?? 0}</div>
              <div className="queue-card-label">{label}</div>
            </div>
          ))}
        </div>
        <div className="channel-row">
          <span>Worker уведомлений</span>
          {workerPill(diagnostics.worker)}
        </div>
        {diagnostics.stuck_sending > 0 && (
          <div className="channel-row">
            <span>Зависших отправок (просроченный lease)</span>
            <span className="status-pill warn">{diagnostics.stuck_sending}</span>
          </div>
        )}
        <p className="notif-card-body">
          Диагностика показывает только счётчики и статусы — без текстов, получателей и
          персональных данных.
        </p>
      </div>

      {/* 2. Integration Probes & Status */}
      <div className="pref-card">
        <h3 className="notif-card-title">Каналы интеграций и диагностика подключения</h3>
        <div className="channel-row">
          <span>Внутренние уведомления (In-App)</span>
          <span className="status-pill ok">работает</span>
        </div>

        {/* Telegram Probe */}
        <div className="admin-test-section">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <strong>Telegram Bot API</strong>
              <div className="consent-meta-info">
                Статус в системе:{" "}
                {integrationsStatus?.telegram.configured_in_system ? (
                  <span style={{ color: "var(--ok)" }}>настроен на сервере</span>
                ) : (
                  <span style={{ color: "var(--muted)" }}>токен не задан</span>
                )}
              </div>
            </div>
            <Button
              variant="secondary"
              size="sm"
              loading={testingTelegram}
              onClick={() => void handleTestTelegram()}
            >
              Проверить подключение getMe
            </Button>
          </div>

          {telegramProbeResult && (
            <div
              className={`admin-test-result ${telegramProbeResult.ok ? "success" : "error"}`}
            >
              {telegramProbeResult.ok ? (
                <div>
                  Бот подключен: @{telegramProbeResult.bot_username} (ID:{" "}
                  {telegramProbeResult.bot_id}, Имя: {telegramProbeResult.first_name})
                </div>
              ) : (
                <div>Ошибка проверки: {telegramProbeResult.error}</div>
              )}
            </div>
          )}
        </div>

        {/* SMTP Probe */}
        <div className="admin-test-section">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <strong>SMTP Сервер</strong>
              <div className="consent-meta-info">
                Статус в системе:{" "}
                {integrationsStatus?.email.configured_in_system ? (
                  <span style={{ color: "var(--ok)" }}>настроен на сервере</span>
                ) : (
                  <span style={{ color: "var(--muted)" }}>хост не задан</span>
                )}
              </div>
            </div>
            <Button
              variant="secondary"
              size="sm"
              loading={testingSmtp}
              onClick={() => void handleTestSmtp()}
            >
              Проверить подключение NOOP
            </Button>
          </div>

          {smtpProbeResult && (
            <div className={`admin-test-result ${smtpProbeResult.ok ? "success" : "error"}`}>
              {smtpProbeResult.ok ? (
                <div>
                  SMTP сервер отвечает: {smtpProbeResult.host}:{smtpProbeResult.port} (TLS:{" "}
                  {smtpProbeResult.use_tls ? "да" : "нет"}, STARTTLS:{" "}
                  {smtpProbeResult.use_starttls ? "да" : "нет"}, Авторизация:{" "}
                  {smtpProbeResult.authenticated ? "пройдена" : "анонимная"})
                </div>
              ) : (
                <div>Ошибка проверки SMTP: {smtpProbeResult.error}</div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 3. Admin Test Send */}
      <div className="pref-card">
        <h3 className="notif-card-title">Тестовая отправка сообщения</h3>
        <p className="notif-card-body">
          Прямая тестовая отправка для проверки каналов связи. Статус <code>accepted</code> означает
          приём сообщения шлюзом провайдера, но не гарантирует немедленного прочтения или доставки в ящик.
        </p>

        <form
          className="reminder-form"
          data-testid="admin-test-send-form"
          onSubmit={(e) => {
            e.preventDefault();
            void handleTestSend();
          }}
        >
          <Field label="Канал доставки" required>
            {(id) => (
              <SelectInput
                id={id}
                value={testSendChannel}
                onChange={(e) => setTestSendChannel(e.target.value as "telegram" | "email")}
              >
                <option value="telegram">Telegram</option>
                <option value="email">Email (SMTP)</option>
              </SelectInput>
            )}
          </Field>

          <Field
            label={testSendChannel === "telegram" ? "Получатель (числовой Chat ID)" : "Получатель (Email)"}
            hint={
              testSendChannel === "telegram"
                ? "Оставьте пустым для отправки в ваш привязанный Telegram"
                : "Оставьте пустым для отправки на ваш email из профиля"
            }
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                placeholder={testSendChannel === "telegram" ? "123456789" : "user@company.com"}
                value={testSendRecipient}
                onChange={(e) => setTestSendRecipient(e.target.value)}
              />
            )}
          </Field>

          <Field label="Тема (Subject)">
            {(id) => (
              <TextInput
                id={id}
                placeholder="Тестовое уведомление HR Manager"
                value={testSendSubject}
                onChange={(e) => setTestSendSubject(e.target.value)}
              />
            )}
          </Field>

          <div className="field-span-2">
            <Field label="Текст сообщения">
              {(id) => (
                <TextAreaInput
                  id={id}
                  placeholder="Проверочное сообщение..."
                  value={testSendBody}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                    setTestSendBody(e.target.value)
                  }
                />
              )}
            </Field>
          </div>

          <div style={{ display: "flex", alignItems: "flex-end" }}>
            <Button type="submit" variant="primary" loading={sendingTest}>
              Отправить тест
            </Button>
          </div>
        </form>

        {testSendResult && (
          <div className="admin-test-result success">
            <div>
              <strong>Результат отправки:</strong> {testSendResult.message}
            </div>
            {testSendResult.provider_message_id && (
              <div className="consent-meta-info" style={{ marginTop: "4px" }}>
                ID сообщения провайдера (Message-ID): {testSendResult.provider_message_id}
              </div>
            )}
          </div>
        )}
      </div>

      {/* 4. Pilot User Management */}
      <div className="pref-card">
        <h3 className="notif-card-title">Пилотный пользователь</h3>
        {setup.pilot_grant_active ? (
          <p className="notif-card-body">
            Пилот назначен: один аккаунт с совмещённым доступом HR, руководителя и
            администратора. Доступ назначен явно и проверяется backend-ом; аудит и CSRF не
            отключаются.
          </p>
        ) : (
          <p className="notif-card-body">
            Пилот ещё не назначен. Создайте один аккаунт с полным доступом — повторный
            запуск мастера не сбросит пароль и не расширит права молча.
          </p>
        )}
        {!setup.pilot_grant_active && (
          <form
            className="reminder-form"
            onSubmit={(event) => {
              event.preventDefault();
              void createPilotAccount();
            }}
            aria-label="Создание пилотного пользователя"
          >
            <Field label="Имя пользователя" required>
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  value={pilotForm.username}
                  onChange={(event) => setPilotForm({ ...pilotForm, username: event.target.value })}
                />
              )}
            </Field>
            <Field label="Пароль (не показывается повторно)" required>
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  type="password"
                  autoComplete="new-password"
                  value={pilotForm.password}
                  onChange={(event) => setPilotForm({ ...pilotForm, password: event.target.value })}
                />
              )}
            </Field>
            <Field label="Полное имя (необязательно)">
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  value={pilotForm.full_name}
                  onChange={(event) => setPilotForm({ ...pilotForm, full_name: event.target.value })}
                />
              )}
            </Field>
            <div style={{ display: "flex", alignItems: "flex-end" }}>
              <Button type="submit" variant="primary" loading={savingPilot}>
                Создать пилота
              </Button>
            </div>
          </form>
        )}
        {grants.length > 0 && (
          <ul className="notif-list">
            {grants.map((grant) => (
              <li key={grant.id} className="notif-card">
                <div className="notif-card-main">
                  <h3 className="notif-card-title">{grant.username}</h3>
                  <div className="notif-card-meta">
                    <span>выдано: {new Date(grant.granted_at).toLocaleString("ru-RU")}</span>
                    {grant.revoked_at ? (
                      <span>отозвано: {new Date(grant.revoked_at).toLocaleString("ru-RU")}</span>
                    ) : (
                      <span>действует</span>
                    )}
                  </div>
                </div>
                {!grant.revoked_at && (
                  <div className="notif-card-actions">
                    <Button variant="ghost" size="sm" onClick={() => void revoke(grant.user_id)}>
                      Отозвать
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
