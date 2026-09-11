/** Обновления Windows-пилота (Phase 13): управляемый канал доставки.

 * Сервер проверяет подписанный manifest доверенными ключами, скачивает
 * пакет в staging и передаёт команду установки Windows-движку, который
 * переиспользует Phase 12 update engine (backup gate → миграция → smoke →
 * rollback). Фоновая установка не запускается никогда: только явное
 * действие администратора. Состояния и итоги — честные (updated /
 * rolled_back / manual_action_required), без универсального «успешно».
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  checkUpdates,
  downloadUpdate,
  fetchUpdateStatus,
  requestUpdateInstall,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type { UpdateStatus } from "../../types";
import "./updates.css";

const STATE_LABELS: Record<string, string> = {
  idle: "Ожидание",
  checking: "Проверка обновлений…",
  up_to_date: "Установлена актуальная версия",
  available: "Доступна новая версия",
  downloading: "Скачивание и проверка пакета…",
  ready: "Пакет проверен и готов к установке",
  installing: "Идёт установка…",
  restart_required: "Требуется перезапуск приложения",
  failed: "Ошибка",
  manual_action_required: "Требуется ручное обновление",
};

const ERROR_LABELS: Record<string, string> = {
  channel_not_configured: "Канал обновлений не настроен.",
  channel_offline: "Канал обновлений недоступен (проверьте сеть).",
  manifest_bad_signature: "Подпись manifest недействительна.",
  manifest_unknown_key: "Ключ подписи не входит в доверенный набор.",
  manifest_revoked_key: "Ключ подписи отозван.",
  manifest_invalid: "Manifest канала некорректен.",
  package_download_failed: "Не удалось скачать пакет.",
  package_hash_mismatch: "Хеш пакета не совпал с manifest.",
  package_extract_failed: "Не удалось распаковать пакет.",
  staging_failed: "Не удалось подготовить staging.",
  update_failed: "Установка не удалась.",
  downgrade_blocked: "Откат версии запрещён.",
  version_conflict: "Конфликт целостности версии.",
  manual_action_required: "Требуется ручное обновление.",
  action_in_progress: "Действие уже выполняется.",
  engine_failed: "Движок обновления сообщил об ошибке.",
};

interface UpdateChannelPageProps {
  onStateChanged?: (state: string) => void;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ru-RU");
}

export function UpdateChannelPage({ onStateChanged }: UpdateChannelPageProps) {
  const { pushToast } = useToast();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await fetchUpdateStatus();
      setStatus(next);
      setError(false);
      onStateChanged?.(next.state);
    } catch (err) {
      if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 403) {
        setConflict("Недостаточно прав для управления каналом обновлений.");
      } else {
        setError(true);
      }
    } finally {
      setLoading(false);
    }
  }, [onStateChanged]);

  useEffect(() => {
    void load();
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [load]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  /** Пока идёт установка — честный опрос состояния (без фиктивных %). */
  const startPolling = useCallback(() => {
    stopPolling();
    pollTimer.current = setInterval(() => {
      void load();
    }, 5000);
  }, [load, stopPolling]);

  const run = useCallback(
    async (kind: "check" | "download" | "install") => {
      setBusy(kind);
      setConflict(null);
      try {
        if (kind === "install") {
          // install: команда поставлена в очередь движку; ждём результат.
          await requestUpdateInstall();
          setStatus({ ...(status ?? (await fetchUpdateStatus())), state: "installing" });
          startPolling();
          pushToast("info", "Установка запущена. Приложение будет кратко недоступно.");
        } else {
          const next = kind === "check" ? await checkUpdates() : await downloadUpdate();
          setStatus(next);
          onStateChanged?.(next.state);
        }
      } catch (err) {
        if (err && typeof err === "object" && "status" in err) {
          const code = (err as { status: number }).status;
          if (code === 403) {
            setConflict("Недостаточно прав для управления каналом обновлений.");
          } else if (code === 409) {
            setConflict("Действие уже выполняется или недоступно сейчас. Обновите состояние.");
          } else if (code === 429) {
            setConflict("Слишком много запросов — повторите позже.");
          } else {
            setConflict("Не удалось выполнить действие. Повторите попытку.");
          }
        } else {
          setConflict("Сеть недоступна. Установленное приложение продолжает работать.");
        }
      } finally {
        setBusy(null);
      }
    },
    [onStateChanged, pushToast, startPolling, status]
  );

  if (loading) {
    return (
      <section className="panel updates-panel" aria-busy="true">
        <SkeletonRows rows={4} />
      </section>
    );
  }
  if (error || !status) {
    return (
      <section className="panel updates-panel">
        <h2 id="updates-title">Обновления приложения</h2>
        <p className="update-result warn" role="alert">
          {conflict ??
            "Не удалось получить состояние канала обновлений. Установленное приложение продолжает работать."}
        </p>
        <div className="update-actions">
          <Button variant="secondary" icon="loader" onClick={() => void load()}>
            Повторить попытку
          </Button>
        </div>
      </section>
    );
  }

  const state = status.state;
  const canCheck = busy === null && !["checking", "downloading", "installing"].includes(state);
  const canDownload = busy === null && state === "available";
  const canInstall = busy === null && state === "ready";

  return (
    <section className="panel updates-panel" aria-labelledby="updates-title">
      <h2 id="updates-title">Обновления приложения</h2>

      <div className="update-status-row" role="status" aria-live="polite">
        <span className={`update-pill ${state === "failed" || state === "manual_action_required" ? "warn" : state === "up_to_date" ? "ok" : "info"}`}>
          {STATE_LABELS[state] ?? state}
        </span>
      </div>

      <dl className="update-facts">
        <div>
          <dt>Установленная версия</dt>
          <dd>
            {status.installed_version}
            {status.installed_release_sha && (
              <span className="update-sha"> (commit {status.installed_release_sha.slice(0, 12)})</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Последняя проверка</dt>
          <dd>{formatDate(status.last_check_at)}</dd>
        </div>
        {status.available_version && (
          <>
            <div>
              <dt>Доступная версия</dt>
              <dd>
                {status.available_version}
                {status.available_published_at && ` от ${formatDate(status.available_published_at)}`}
              </dd>
            </div>
            {status.notes_ru && (
              <div>
                <dt>Что нового</dt>
                <dd className="update-notes">{status.notes_ru}</dd>
              </div>
            )}
          </>
        )}
      </dl>

      {state === "downloading" && (
        <p className="muted" role="status">
          Пакет скачивается и проверяется по размеру и SHA256…
        </p>
      )}

      {state === "installing" || state === "restart_required" ? (
        <p className="muted" role="status" aria-live="polite">
          {state === "installing"
            ? "Установка выполняется Windows-движком: перед миграцией создаётся проверенный шифрованный бэкап; при сбое приложение автоматически вернётся к прежней версии."
            : "Обновление применено. Перезапустите приложение, чтобы завершить."}
        </p>
      ) : null}

      {status.last_result === "updated" && (
        <p className="update-result ok" role="status">
          Обновление завершено успешно.
        </p>
      )}
      {status.last_result === "rolled_back" && (
        <p className="update-result warn" role="alert">
          Установка не удалась — приложение автоматически вернулось к прежней рабочей версии.
        </p>
      )}
      {status.last_result === "restart_required" && (
        <p className="update-result ok" role="status">
          Обновление применено; требуется перезапуск приложения.
        </p>
      )}

      {status.error_code && ERROR_LABELS[status.error_code] && (
        <p className="update-result warn" role="alert">
          {ERROR_LABELS[status.error_code]}
        </p>
      )}
      {state === "manual_action_required" && (
        <p className="update-result warn" role="alert">
          Текущая версия ниже минимально поддерживаемой — обновление требует ручных действий
          администратора (см. документацию установщика).
        </p>
      )}

      {conflict && (
        <p className="update-result warn" role="alert">
          {conflict}
        </p>
      )}

      {!status.channel_configured && (
        <p className="muted">Канал обновлений не настроен в конфигурации сервера.</p>
      )}

      <div className="update-actions">
        <Button variant="secondary" disabled={!canCheck} onClick={() => void run("check")}>
          {busy === "check" ? "Проверяем…" : "Проверить обновления"}
        </Button>
        <Button variant="secondary" disabled={!canDownload} onClick={() => void run("download")}>
          {busy === "download" ? "Скачиваем…" : "Скачать"}
        </Button>
        <Button variant="primary" disabled={!canInstall} onClick={() => void run("install")}>
          {busy === "install" ? "Запускаем…" : "Установить"}
        </Button>
      </div>

      {canInstall && (
        <p className="muted">
          Перед установкой будет создан проверенный шифрованный бэкап. Приложение будет кратко
          недоступно; при сбое обновление автоматически откатится к прежней версии.
        </p>
      )}
    </section>
  );
}
