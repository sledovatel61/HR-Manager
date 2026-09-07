/** Admin: notification queue diagnostics + pilot setup.

 * Diagnostics are counters and statuses only (the backend never returns
 * PII here). The pilot block creates the single pilot account with an
 * explicit full-access grant — idempotent, audited, never resets an
 * existing password. Channel status is honest: Telegram/email are
 * «not_configured» until phase 9.
 */

import { useCallback, useEffect, useState } from "react";
import {
  createPilot,
  fetchQueueDiagnostics,
  fetchSetupState,
  listAccessGrants,
  revokePilotAccess,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, TextInput } from "../../design-system/components/Field";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type { AccessGrant, QueueDiagnostics, SetupState } from "../../types";
import "./notifications.css";

const STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  sending: "Отправляется",
  accepted: "Принято",
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
  return <span className="status-pill warn">требуется действие: worker не запущен или не отвечает</span>;
}

export function AdminQueuePage() {
  const { pushToast } = useToast();
  const [diagnostics, setDiagnostics] = useState<QueueDiagnostics | null>(null);
  const [setup, setSetup] = useState<SetupState | null>(null);
  const [grants, setGrants] = useState<AccessGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [pilotForm, setPilotForm] = useState({ username: "", password: "", full_name: "" });
  const [savingPilot, setSavingPilot] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [diag, state, grantList] = await Promise.all([
        fetchQueueDiagnostics(),
        fetchSetupState(),
        listAccessGrants(),
      ]);
      setDiagnostics(diag);
      setSetup(state);
      setGrants(grantList.items);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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

      <div className="pref-card">
        <h3 className="notif-card-title">Каналы интеграций</h3>
        <div className="channel-row">
          <span>Внутренние уведомления</span>
          <span className="status-pill ok">работает</span>
        </div>
        {(Object.entries(setup.channels) as Array<[string, string]>).map(([channel, state]) => (
          <div className="channel-row" key={channel}>
            <span>{channel === "telegram" ? "Telegram" : "Email"}</span>
            <span className="status-pill neutral">
              {state === "not_configured" ? "не настроено" : state}
            </span>
          </div>
        ))}
        <p className="notif-card-body">
          Отсутствие внешних каналов не мешает внутренним уведомлениям. Подключение —
          следующий этап.
        </p>
      </div>

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
