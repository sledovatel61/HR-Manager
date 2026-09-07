/** First-login setup wizard: timezone + quiet hours quick-set.

 * Shown once (while preferences are not initialized). One compact
 * scenario: pick a timezone, pick a quiet-hours preset (or disable),
 * save or skip. The wizard is safe to interrupt and resume — nothing is
 * half-applied, and re-running never resets existing settings.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchSetupState, getPreferences, listTimezones, savePreferences } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import { useToast } from "../../design-system/components/ToastContext";
import "./notifications.css";

const PRESETS = [
  { label: "Стандарт: 21:00–08:00", start: "21:00", end: "08:00" },
  { label: "Вечер: 20:00–09:00", start: "20:00", end: "09:00" },
  { label: "Отключить тихие часы", start: "09:00", end: "09:00" },
];

export function SetupWizard({ onDone }: { onDone: () => void }) {
  const { pushToast } = useToast();
  const [open, setOpen] = useState(false);
  const [checked, setChecked] = useState(false);
  const [timezone, setTimezone] = useState("Europe/Moscow");
  const [preset, setPreset] = useState(0);
  const [timezones, setTimezones] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [state, prefs, zones] = await Promise.all([
          fetchSetupState(),
          getPreferences(),
          listTimezones(),
        ]);
        if (cancelled) return;
        setTimezone(prefs.timezone);
        setTimezones(zones.timezones);
        setOpen(!prefs.initialized);
        setChecked(true);
        // The pilot/worker part of the state is shown in the admin screen;
        // the wizard itself only handles the user's own settings.
        void state;
      } catch {
        if (!cancelled) setChecked(true); // backend unreachable → no wizard
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const presetDef = PRESETS[preset];
      const prefs = await getPreferences();
      await savePreferences({
        timezone,
        quiet_hours_start: presetDef.start,
        quiet_hours_end: presetDef.end,
        workdays: prefs.workdays,
        enabled_types: prefs.enabled_types,
        enabled_channels: prefs.enabled_channels,
      });
      pushToast("success", "Настройки сохранены. Их можно изменить в разделе «Настройки уведомлений».");
      setOpen(false);
      onDone();
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Не удалось сохранить.");
    } finally {
      setSaving(false);
    }
  }, [onDone, preset, pushToast, timezone]);

  if (!checked) return null;

  return (
    <Modal
      open={open}
      title="Добро пожаловать — быстрая настройка"
      description="Один шаг: часовая зона и тихие часы для уведомлений. Всё можно пропустить и настроить позже."
      onClose={() => {
        setOpen(false);
        onDone();
      }}
      footer={
        <>
          <Button variant="secondary" onClick={() => { setOpen(false); onDone(); }}>
            Пропустить
          </Button>
          <Button variant="primary" loading={saving} onClick={() => void save()}>
            Сохранить
          </Button>
        </>
      }
    >
      <div className="pref-card">
        <Field label="Часовая зона">
          {(id) => (
            <SelectInput id={id} value={timezone} onChange={(event) => setTimezone(event.target.value)}>
              {timezones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <Field label="Тихие часы" hint="Автоматические сообщения не приходят в этот период; интервал может пересекать полночь.">
          {(id) => (
            <SelectInput id={id} value={String(preset)} onChange={(event) => setPreset(Number(event.target.value))}>
              {PRESETS.map((item, index) => (
                <option key={item.label} value={String(index)}>
                  {item.label}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <p className="notif-card-body">
          Telegram и email пока не настроены — внутренние уведомления работают без них.
        </p>
      </div>
    </Modal>
  );
}
