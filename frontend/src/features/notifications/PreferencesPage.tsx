/** «Настройки уведомлений»: timezone, quiet hours, workdays, types.

 * Quiet hours accept a midnight-crossing interval (e.g. 21:00–08:00);
 * the backend evaluates them in the user's IANA timezone and handles
 * DST. Type toggles decide which logical notifications may be delivered.
 */

import { useCallback, useEffect, useState } from "react";
import { getPreferences, listTimezones, savePreferences } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type { NotificationPreferences } from "../../types";
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

export function PreferencesPage() {
  const { pushToast } = useToast();
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [timezones, setTimezones] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);

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
      });
      setPreferences(saved);
      pushToast("success", "Настройки сохранены.");
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Не удалось сохранить.");
    } finally {
      setSaving(false);
    }
  }, [preferences, pushToast]);

  if (loading) return <SkeletonRows rows={4} columns={3} />;
  if (error || !preferences) return <ErrorState onRetry={() => void load()} />;

  return (
    <div className="notif-page" aria-live="polite">
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
          <Field label="Тихие часы: конец (местное время)" hint="Например, 21:00–08:00 — интервал через полночь.">
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

      <div className="pref-card">
        <h3 className="notif-card-title">Каналы доставки</h3>
        <p className="notif-card-body">
          Внутренние уведомления работают всегда. Telegram и email привязываются отдельно — только
          после вашей привязки и явного согласия.
        </p>
        <div className="channel-row">
          <span>Внутренние уведомления</span>
          <span className="status-pill ok">работает</span>
        </div>
        <div className="channel-row">
          <span>Telegram и email</span>
          <a href="#/integrations">настроить в разделе «Интеграции»</a>
        </div>
      </div>

      <div>
        <Button variant="primary" loading={saving} onClick={() => void save()}>
          Сохранить настройки
        </Button>
      </div>
    </div>
  );
}
