/** Phase 12: first-run screen for the local Windows pilot.

 * The installer opens `http://127.0.0.1/#setup=<ticket>`; this screen:
 *  1. reads the short-lived ticket ONLY from the URL fragment (never from
 *     query parameters or logs),
 *  2. previews the surname/working mode collected by the installer,
 *  3. lets the pilot choose the password (never on a command line) plus
 *     timezone/workdays/quiet hours,
 *  4. redeems the one-time ticket — the backend creates the single pilot
 *     owner (server role admin + explicit pilot_full_access grant) and
 *     opens the session.
 *
 * Email/Telegram are optional and configured later in the notification
 * settings; the first run never blocks on them.
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { listTimezones, previewOwnerSetup, redeemOwnerSetup } from "../api";
import type { CurrentUser, SetupPreview } from "../types";
import { WORKING_MODE_LABELS } from "../types";
import { Button } from "../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../design-system/components/Field";
import "./first-run.css";

const DEFAULT_WORKDAYS = [1, 2, 3, 4, 5]; // Monday..Friday

const QUIET_HOUR_PRESETS = [
  { id: "standard", label: "Стандарт: 21:00–08:00", start: "21:00", end: "08:00" },
  { id: "evening", label: "Вечер: 20:00–09:00", start: "20:00", end: "09:00" },
  { id: "off", label: "Без тихих часов", start: "09:00", end: "09:00" },
];

const WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

type Phase = "loading" | "form" | "submitting" | "blocked";

interface FirstRunSetupProps {
  ticket: string;
  onComplete: (current: CurrentUser) => void;
  /** Injectable for tests; defaults to the real API calls. */
  previewFetcher?: (ticket: string) => Promise<SetupPreview>;
  redeemFetcher?: typeof redeemOwnerSetup;
}

/** First-run wizard. The ticket is consumed exactly once by redeem; after
 * success the URL fragment is cleared so the one-time secret does not
 * linger in the address bar or history. */
export default function FirstRunSetup({
  ticket,
  onComplete,
  previewFetcher = previewOwnerSetup,
  redeemFetcher = redeemOwnerSetup,
}: FirstRunSetupProps) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [preview, setPreview] = useState<SetupPreview | null>(null);
  const [timezones, setTimezones] = useState<string[]>([]);
  const [timezone, setTimezone] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [workdays, setWorkdays] = useState<number[]>(DEFAULT_WORKDAYS);
  const [quietPreset, setQuietPreset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        // The timezone catalogue belongs to authenticated preferences. During
        // first-run there is no session yet, so a 401 here must not block the
        // owner form. The ticket preview already supplies a validated default.
        const [data, zones] = await Promise.all([
          previewFetcher(ticket),
          listTimezones().catch(() => null),
        ]);
        if (cancelled) return;
        setPreview(data);
        setTimezones(zones?.timezones ?? [data.timezone]);
        setTimezone(data.timezone);
        setPhase("form");
      } catch (caught) {
        if (cancelled) return;
        // 409: the first run already completed; 403/410: unknown/expired
        // ticket; 404: this deployment has no installer bootstrap at all.
        setBlockedMessage(
          caught instanceof Error ? caught.message : "Первоначальная настройка недоступна.",
        );
        setPhase("blocked");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ticket, previewFetcher]);

  const toggleWorkday = (day: number) => {
    setWorkdays((current) =>
      current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort(),
    );
  };

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setError(null);
      if (password.length < 12) {
        setError("Пароль должен быть не короче 12 символов.");
        return;
      }
      if (password !== confirmPassword) {
        setError("Пароли не совпадают.");
        return;
      }
      if (workdays.length === 0) {
        setError("Выберите хотя бы один рабочий день.");
        return;
      }
      const preset = QUIET_HOUR_PRESETS[quietPreset];
      setPhase("submitting");
      try {
        const current = await redeemFetcher({
          ticket,
          timezone,
          workdays,
          quiet_hours_start: preset.start,
          quiet_hours_end: preset.end,
          password,
        });
        // The one-time ticket must not linger in the address bar.
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
        onComplete(current);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Не удалось завершить настройку.");
        setPhase("form");
      }
    },
    [confirmPassword, onComplete, password, quietPreset, redeemFetcher, ticket, timezone, workdays],
  );

  if (phase === "loading") {
    return (
      <div className="auth-screen">
        <section className="panel first-run-panel">
          <p className="muted" role="status">
            Подготовка первого запуска…
          </p>
        </section>
      </div>
    );
  }

  if (phase === "blocked" || !preview) {
    return (
      <div className="auth-screen">
        <section className="panel first-run-panel">
          <h2 className="panel-title">Первоначальная настройка</h2>
          <p className="panel-subtitle" role="alert">
            {blockedMessage ?? "Первоначальная настройка недоступна."}
          </p>
          <p className="muted">
            Если установка уже завершена — войдите обычным способом. Иначе перезапустите
            приложение «HR Manager» и повторите настройку.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="auth-screen">
      <section className="panel first-run-panel">
        <h2 className="panel-title">Первый запуск HR Manager</h2>
        <p className="panel-subtitle">
          Это компьютер владельца пилотной установки. Завершите настройку — дальше приложение
          будет открываться обычным входом.
        </p>

        <dl className="first-run-summary">
          <div>
            <dt>Фамилия</dt>
            <dd>{preview.surname}</dd>
          </div>
          <div>
            <dt>Роль в системе</dt>
            <dd>
              {WORKING_MODE_LABELS[preview.working_mode] ?? preview.working_mode} · полный
              доступ пилота
            </dd>
          </div>
        </dl>

        <form onSubmit={(event) => void handleSubmit(event)} className="first-run-form">
          <Field label="Пароль владельца" hint="Не короче 12 символов. Хранится только в виде хеша Argon2id." required>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            )}
          </Field>

          <Field label="Повторите пароль" required>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                required
              />
            )}
          </Field>

          <Field label="Часовой пояс" required>
            {(id, describedBy) => (
              <SelectInput
                id={id}
                aria-describedby={describedBy}
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              >
                {timezones.length === 0 && <option value={timezone}>{timezone}</option>}
                {timezones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>

          <fieldset className="first-run-fieldset">
            <legend>Рабочие дни</legend>
            <div className="first-run-weekdays">
              {WEEKDAY_LABELS.map((label, index) => {
                const day = index + 1;
                return (
                  <label key={day} className="first-run-weekday">
                    <input
                      type="checkbox"
                      checked={workdays.includes(day)}
                      onChange={() => toggleWorkday(day)}
                    />
                    <span>{label}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <Field label="Тихие часы" hint="В это время уведомления не отправляются.">
            {(id, describedBy) => (
              <SelectInput
                id={id}
                aria-describedby={describedBy}
                value={quietPreset}
                onChange={(event) => setQuietPreset(Number(event.target.value))}
              >
                {QUIET_HOUR_PRESETS.map((preset, index) => (
                  <option key={preset.id} value={index}>
                    {preset.label}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>

          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}

          <Button type="submit" disabled={phase === "submitting"} className="first-run-submit">
            {phase === "submitting" ? "Создаём рабочее пространство…" : "Завершить настройку"}
          </Button>

          <p className="muted first-run-note">
            Email и Telegram можно подключить позже в разделе «Уведомления». Это необязательно.
          </p>
        </form>
      </section>
    </div>
  );
}
