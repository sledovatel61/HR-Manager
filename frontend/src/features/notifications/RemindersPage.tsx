/** «Мои напоминания»: compact create form + list with complete/cancel.

 * The form is one compact scenario (no cron knowledge): title, note,
 * due date/time, timezone, importance, recurrence and an optional
 * candidate link. The candidate is picked through server-side search by
 * name/phone/email (UX feedback 2026-09-29: «Без кандидата» stays, поиск
 * вместо неподъёмного списка). All times are sent as ISO strings; the backend
 * normalizes them to UTC.
 */

import { useCallback, useEffect, useState } from "react";
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

export function RemindersPage({
  user,
  onOpenCandidate,
}: {
  user: { id: string; role: string };
  /** Cross-section navigation to the candidate card. */
  onOpenCandidate?: (id: string) => void;
}) {
  const { pushToast } = useToast();
  const [status, setStatus] = useState<ReminderStatus>("active");
  const [items, setItems] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [timezones, setTimezones] = useState<string[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState<Reminder | null>(null);
  const [saving, setSaving] = useState(false);

  // Server-side candidate search (name/phone/email) with «Без кандидата».
  const [candidateQuery, setCandidateQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Candidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState<{ id: string; full_name: string } | null>(
    null
  );

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
  }, []);

  // Debounced server-side candidate search (name/phone/email) — права
  // фильтруются на сервере, UI показывает только доступное.
  useEffect(() => {
    const query = candidateQuery.trim();
    if (!query) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSearching(true);
      void listCandidates({ query, limit: 8, sort: "updated_at", direction: "desc" })
        .then((page) => {
          if (!cancelled) setSuggestions(page.items);
        })
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [candidateQuery]);

  const resetForm = useCallback(() => {
    setForm(EMPTY_FORM);
    setEditing(null);
    setSelectedCandidate(null);
    setCandidateQuery("");
    setSuggestions([]);
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
    setSelectedCandidate(
      reminder.candidate_id
        ? { id: reminder.candidate_id, full_name: reminder.candidate_full_name || "Кандидат" }
        : null
    );
    setCandidateQuery("");
    setSuggestions([]);
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

  const pickCandidate = useCallback((candidate: Candidate) => {
    setSelectedCandidate({ id: candidate.id, full_name: candidate.full_name });
    setForm((current) => ({ ...current, candidate_id: candidate.id }));
    setCandidateQuery("");
    setSuggestions([]);
  }, []);

  const clearCandidate = useCallback(() => {
    setSelectedCandidate(null);
    setForm((current) => ({ ...current, candidate_id: "" }));
    setCandidateQuery("");
    setSuggestions([]);
  }, []);

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
        <Field
          label="Кандидат"
          hint="Необязательно. Поиск по ФИО, телефону или email; можно оставить «Без кандидата»"
        >
          {(id, describedBy) => (
            <div className="reminder-candidate-picker">
              {selectedCandidate ? (
                <div className="reminder-candidate-selected">
                  <span>{selectedCandidate.full_name}</span>
                  <Button variant="ghost" size="sm" onClick={clearCandidate}>
                    Без кандидата
                  </Button>
                </div>
              ) : (
                <>
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    value={candidateQuery}
                    onChange={(event) => setCandidateQuery(event.target.value)}
                    placeholder="Поиск по ФИО, телефону или email…"
                  />
                  {searching && <p className="muted-text">Поиск…</p>}
                  {!searching && suggestions.length > 0 && (
                    <ul className="reminder-candidate-list" role="listbox">
                      {suggestions.map((item) => (
                        <li key={item.id}>
                          <button
                            type="button"
                            role="option"
                            aria-selected={false}
                            onClick={() => pickCandidate(item)}
                          >
                            <span>{item.full_name}</span>
                            {item.phone && <span className="reminder-candidate-contact">{item.phone}</span>}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
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
                    {reminder.candidate_id && (
                      <span>кандидат: {reminder.candidate_full_name || "связанный кандидат"}</span>
                    )}
                    {reminder.assignee_user_id !== user.id && (
                      <span>исполнитель: {reminder.assignee_username}</span>
                    )}
                  </div>
                </div>
                <div className="notif-card-actions">
                  {reminder.candidate_id && onOpenCandidate && (
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="users"
                      onClick={() => reminder.candidate_id && onOpenCandidate(reminder.candidate_id)}
                    >
                      Карточка кандидата
                    </Button>
                  )}
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
