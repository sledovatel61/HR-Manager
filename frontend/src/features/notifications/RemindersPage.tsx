/** «Мои напоминания»: compact create form + list with complete/cancel.

 * The form is one compact scenario (no cron knowledge): title, note,
 * due date/time, timezone, importance, recurrence and an optional
 * candidate link. All times are sent as ISO strings; the backend
 * normalizes them to UTC.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelReminder,
  completeReminder,
  createReminder,
  listCandidates,
  listReminders,
  listTimezones,
  updateReminder,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { EmptyState, ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type { Candidate, Reminder, ReminderStatus } from "../../types";
import "./notifications.css";

const RECURRENCE_LABELS: Record<string, string> = {
  none: "Без повторения",
  daily: "Ежедневно",
  workdays: "По рабочим дням",
  weekly: "Еженедельно",
};

const IMPORTANCE_LABELS: Record<string, string> = {
  low: "Низкая",
  normal: "Обычная",
  high: "Высокая",
};

const STATUS_LABELS: Record<ReminderStatus, string> = {
  active: "Активные",
  completed: "Завершённые",
  cancelled: "Отменённые",
};

function toLocalInput(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function formatDue(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

const EMPTY_FORM = {
  title: "",
  note: "",
  due_local: "",
  timezone: "Europe/Moscow",
  importance: "normal",
  recurrence: "none",
  candidate_id: "",
};

export function RemindersPage({ user }: { user: { id: string; role: string } }) {
  const { pushToast } = useToast();
  const [status, setStatus] = useState<ReminderStatus>("active");
  const [items, setItems] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [timezones, setTimezones] = useState<string[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState<Reminder | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const payload = await listReminders({ status, limit: 50 });
      setItems(payload.items);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void listTimezones()
      .then((data) => setTimezones(data.timezones))
      .catch(() => setTimezones(["Europe/Moscow"]));
    void listCandidates({ limit: 200 })
      .then((data) => setCandidates(data.items))
      .catch(() => setCandidates([]));
  }, []);

  const resetForm = useCallback(() => {
    setForm(EMPTY_FORM);
    setEditing(null);
  }, []);

  const submit = useCallback(async () => {
    if (!form.title.trim() || !form.due_local) {
      pushToast("info", "Заполните название и время напоминания.");
      return;
    }
    const dueIso = new Date(form.due_local).toISOString();
    setSaving(true);
    try {
      if (editing) {
        await updateReminder(editing.id, {
          expected_version: editing.version,
          title: form.title.trim(),
          note: form.note || null,
          due_at: dueIso,
          timezone: form.timezone,
          importance: form.importance as Reminder["importance"],
          recurrence: form.recurrence as Reminder["recurrence"],
          candidate_id: form.candidate_id || null,
        });
        pushToast("success", "Напоминание обновлено.");
      } else {
        await createReminder({
          title: form.title.trim(),
          note: form.note || null,
          due_at: dueIso,
          timezone: form.timezone,
          importance: form.importance as Reminder["importance"],
          recurrence: form.recurrence as Reminder["recurrence"],
          candidate_id: form.candidate_id || null,
        });
        pushToast("success", "Напоминание создано.");
      }
      resetForm();
      await load();
    } catch (caught) {
      pushToast("danger", caught instanceof Error ? caught.message : "Не удалось сохранить.");
    } finally {
      setSaving(false);
    }
  }, [editing, form, load, pushToast, resetForm]);

  const startEdit = useCallback((reminder: Reminder) => {
    setEditing(reminder);
    setForm({
      title: reminder.title,
      note: reminder.note ?? "",
      due_local: toLocalInput(reminder.due_at),
      timezone: reminder.timezone,
      importance: reminder.importance,
      recurrence: reminder.recurrence,
      candidate_id: reminder.candidate_id ?? "",
    });
  }, []);

  const complete = useCallback(
    async (id: string) => {
      try {
        await completeReminder(id);
        pushToast("success", "Напоминание завершено.");
        await load();
      } catch {
        pushToast("danger", "Не удалось завершить напоминание.");
      }
    },
    [load, pushToast],
  );

  const cancel = useCallback(
    async (id: string) => {
      try {
        await cancelReminder(id);
        pushToast("success", "Напоминание отменено.");
        await load();
      } catch {
        pushToast("danger", "Не удалось отменить напоминание.");
      }
    },
    [load, pushToast],
  );

  const candidateOptions = useMemo(
    () =>
      candidates.map((candidate) => (
        <option key={candidate.id} value={candidate.id}>
          {candidate.full_name}
        </option>
      )),
    [candidates],
  );

  return (
    <div className="notif-page">
      <form
        className="reminder-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        aria-label={editing ? "Редактирование напоминания" : "Новое напоминание"}
      >
        <Field label="Название" required>
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              value={form.title}
              maxLength={200}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              placeholder="Например: позвонить кандидату"
            />
          )}
        </Field>
        <Field label="Когда (локальное время)" required>
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              type="datetime-local"
              value={form.due_local}
              onChange={(event) => setForm({ ...form, due_local: event.target.value })}
            />
          )}
        </Field>
        <Field label="Часовая зона">
          {(id) => (
            <SelectInput
              id={id}
              value={form.timezone}
              onChange={(event) => setForm({ ...form, timezone: event.target.value })}
            >
              {timezones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <Field label="Повторение">
          {(id) => (
            <SelectInput
              id={id}
              value={form.recurrence}
              onChange={(event) => setForm({ ...form, recurrence: event.target.value })}
            >
              {Object.entries(RECURRENCE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <Field label="Важность">
          {(id) => (
            <SelectInput
              id={id}
              value={form.importance}
              onChange={(event) => setForm({ ...form, importance: event.target.value })}
            >
              {Object.entries(IMPORTANCE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <Field label="Кандидат (необязательно)">
          {(id) => (
            <SelectInput
              id={id}
              value={form.candidate_id}
              onChange={(event) => setForm({ ...form, candidate_id: event.target.value })}
            >
              <option value="">— без кандидата —</option>
              {candidateOptions}
            </SelectInput>
          )}
        </Field>
        <Field label="Заметка">
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              value={form.note}
              maxLength={2000}
              onChange={(event) => setForm({ ...form, note: event.target.value })}
              placeholder="Пара слов для себя (необязательно)"
            />
          )}
        </Field>
        <div className="field-span-2" style={{ display: "flex", gap: 10 }}>
          <Button type="submit" variant="primary" loading={saving}>
            {editing ? "Сохранить изменения" : "Создать напоминание"}
          </Button>
          {editing && (
            <Button variant="secondary" onClick={resetForm}>
              Отменить редактирование
            </Button>
          )}
        </div>
      </form>

      <div className="notif-toolbar" role="tablist" aria-label="Фильтр по статусу">
        {(Object.keys(STATUS_LABELS) as ReminderStatus[]).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={status === value}
            className={`toggle-chip ${status === value ? "is-active" : ""}`}
            onClick={() => setStatus(value)}
          >
            {STATUS_LABELS[value]}
          </button>
        ))}
      </div>

      <div aria-live="polite">
        {loading ? (
          <SkeletonRows rows={4} columns={4} />
        ) : error ? (
          <ErrorState onRetry={() => void load()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon="clock"
            title="Напоминаний нет"
            description="Создайте первое напоминание — форма выше занимает меньше минуты."
          />
        ) : (
          <ul className="notif-list">
            {items.map((reminder) => (
              <li key={reminder.id} className="notif-card">
                <div className="notif-card-main">
                  <h3 className="notif-card-title">{reminder.title}</h3>
                  {reminder.note && <p className="notif-card-body">{reminder.note}</p>}
                  <div className="notif-card-meta">
                    <span>{formatDue(reminder.due_at)}</span>
                    <span>{RECURRENCE_LABELS[reminder.recurrence]}</span>
                    <span>{IMPORTANCE_LABELS[reminder.importance]}</span>
                    {reminder.assignee_user_id !== user.id && (
                      <span>исполнитель: {reminder.assignee_username}</span>
                    )}
                  </div>
                </div>
                <div className="notif-card-actions">
                  {reminder.status === "active" && (
                    <>
                      <Button variant="secondary" size="sm" onClick={() => startEdit(reminder)}>
                        Изменить
                      </Button>
                      <Button variant="secondary" size="sm" onClick={() => void complete(reminder.id)}>
                        Выполнено
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => void cancel(reminder.id)}>
                        Отменить
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
