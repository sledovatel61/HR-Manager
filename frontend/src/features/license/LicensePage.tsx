/** Лицензия — админский экран для закрытого пилота.
 * Только admin может загрузить/заменить лицензию.
 * Офлайн: файл .hrmlicense или JSON вставка.
 * Русские сообщения, честные статусы, без секретов в UI.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchLicenseStatus, uploadLicenseFile, uploadLicenseJson } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field } from "../../design-system/components/Field";
import type { LicenseStatus } from "../../types";
import "./license.css";

interface LicensePageProps {
  fetcher?: () => Promise<LicenseStatus>;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU");
}

function formatDaysLeft(days: number | null | undefined): string {
  if (days == null) return "—";
  if (days < 0) return `истёк ${Math.abs(days)} дн. назад`;
  if (days === 0) return "истекает сегодня";
  if (days === 1) return "1 день";
  if (days <= 4) return `${days} дня`;
  return `${days} дней`;
}

export function LicensePage({ fetcher }: LicensePageProps) {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [jsonText, setJsonText] = useState("");
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const fn = fetcher ?? fetchLicenseStatus;
      const data = await fn();
      setStatus(data);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Ошибка загрузки статуса лицензии";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleUpload = async () => {
    setUploadError(null);
    setUploadSuccess(null);
    if (!file && !jsonText.trim()) {
      setUploadError("Выберите файл .hrmlicense или вставьте JSON лицензии.");
      return;
    }
    setUploading(true);
    try {
      if (file) {
        await uploadLicenseFile(file);
      } else {
        const trimmed = jsonText.trim();
        try {
          const parsed = JSON.parse(trimmed) as Record<string, unknown>;
          if (
            parsed &&
            typeof parsed === "object" &&
            "license_id" in parsed
          ) {
            await uploadLicenseJson({ license: parsed });
          } else {
            await uploadLicenseJson({ license_text: trimmed });
          }
        } catch {
          await uploadLicenseJson({ license_text: trimmed });
        }
      }
      setUploadSuccess("Лицензия успешно загружена и активирована.");
      const fn = fetcher ?? fetchLicenseStatus;
      const fresh = await fn();
      setStatus(fresh);
      setFile(null);
      setJsonText("");
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : "Ошибка загрузки лицензии";
      setUploadError(msg);
    } finally {
      setUploading(false);
    }
  };

  if (loading) {
    return (
      <div className="license-page" aria-label="Лицензия">
        <p className="muted">Загрузка статуса лицензии…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="license-page" aria-label="Лицензия">
        <div role="alert" className="license-alert license-alert--error">
          <p>{error}</p>
          <Button onClick={() => void load()}>Повторить попытку</Button>
        </div>
      </div>
    );
  }

  const enforcement = status?.enforcement ?? "unknown";
  const hasLicense = status?.has_license;
  const isValid = status?.is_valid;
  const lic = status?.license;
  const activeUsers = lic?.active_users ?? status?.active_users;
  const code = status?.code;
  const message = status?.message;

  return (
    <div className="license-page" aria-label="Лицензия">
      <h2>Лицензия</h2>

      {enforcement === "disabled" && (
        <div className="license-alert license-alert--warn">
          <p>Проверка лицензии отключена (dev/test). В пилоте должен быть установлен публичный ключ.</p>
        </div>
      )}

      {!hasLicense && (
        <div role="alert" className="license-alert license-alert--error">
          <p>Лицензия не установлена. Загрузите файл лицензии, выданный владельцем.</p>
          {message && <p className="muted">{message}</p>}
        </div>
      )}

      {hasLicense && !isValid && (
        <div role="alert" className="license-alert license-alert--error">
          <p>Лицензия недействительна: {code ?? "unknown"}</p>
          {message && <p>{message}</p>}
          {code === "expired" && <p>Срок лицензии истёк. Администратор может загрузить новую лицензию — данные не удаляются.</p>}
        </div>
      )}

      {hasLicense && isValid && lic && (
        <div className="license-card">
          <h3>Текущая лицензия</h3>
          <dl className="license-details">
            <dt>Клиент/пилот</dt>
            <dd>{lic.client_name}</dd>
            <dt>ID лицензии</dt>
            <dd><code>{lic.license_id}</code></dd>
            <dt>Выдана</dt>
            <dd>{formatDate(lic.issued_at)}</dd>
            <dt>Действует до</dt>
            <dd>{lic.expires_at} (включительно до конца дня по UTC)</dd>
            <dt>Дней осталось</dt>
            <dd>{formatDaysLeft(lic.days_left)}</dd>
            <dt>Лимит пользователей</dt>
            <dd>{lic.max_active_users} (включает администратора)</dd>
            <dt>Активных пользователей</dt>
            <dd>{activeUsers ?? "—"}</dd>
            {lic.last_seen_at && (
              <>
                <dt>Последняя проверка</dt>
                <dd>{formatDate(lic.last_seen_at)}</dd>
              </>
            )}
          </dl>
          {status?.public_key_fingerprint && (
            <p className="muted">Отпечаток ключа: {status.public_key_fingerprint}</p>
          )}
        </div>
      )}

      {!lic && hasLicense && status?.license && (
        <div className="license-card">
          <h3>Текущая лицензия</h3>
          <pre className="license-pre">{JSON.stringify(status.license, null, 2)}</pre>
        </div>
      )}

      <div className="license-upload">
        <h3>Загрузить новую лицензию (только администратор)</h3>
        <p className="muted">
          Выберите файл <code>.hrmlicense</code>, выданный владельцем, или вставьте JSON. При истечении срока — загрузите новую, данные не удаляются, переустановка не нужна.
        </p>

        <Field label="Файл лицензии (.hrmlicense, JSON)">
          {(id) => (
            <>
              <input
                id={id}
                type="file"
                accept=".hrmlicense,.json,application/json"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              {file && <p className="muted">Выбран: {file.name} ({Math.round(file.size / 1024)} КБ)</p>}
            </>
          )}
        </Field>

        <Field label="Или вставьте JSON лицензии">
          {(id) => (
            <textarea
              id={id}
              rows={10}
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              placeholder='{"license_id":"...","client_name":"Пилот Марии","issued_at":"...","expires_at":"2026-12-31","max_active_users":5,"signature":"..."}'
            />
          )}
        </Field>

        {uploadError && (
          <div role="alert" className="license-alert license-alert--error">
            <p>{uploadError}</p>
          </div>
        )}
        {uploadSuccess && (
          <div role="status" className="license-alert license-alert--ok">
            <p>{uploadSuccess}</p>
          </div>
        )}

        <div className="license-actions">
          <Button onClick={() => void handleUpload()} disabled={uploading}>
            {uploading ? "Загрузка…" : "Загрузить лицензию"}
          </Button>
          <Button variant="ghost" onClick={() => void load()} disabled={uploading}>
            Обновить статус
          </Button>
        </div>

        <div className="license-help">
          <h4>Справка</h4>
          <ul>
            <li>Дата <code>expires_at</code> действует включительно до конца дня по UTC.</li>
            <li>Лимит включает администратора (Марию). При превышении лимита — сначала отключите лишних пользователей.</li>
            <li>При истечении — обычная работа останавливается (403), но админ может войти и загрузить новую лицензию.</li>
            <li>Файл лицензии — офлайн, подпись Ed25519, без обращения к интернету.</li>
            <li>Защита от перевода часов: сервер помнит <code>last_seen_at</code> и блокирует лицензию при откате времени более чем на 1 час (best-effort).</li>
            <li>Если загрузка не удаётся: проверьте, что файл не изменён и выдан владельцем, и что публичный ключ на сервере совпадает.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
