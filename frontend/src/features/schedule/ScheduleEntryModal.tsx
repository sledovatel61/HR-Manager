import { useEffect, useState } from "react";
import { ApiError, createScheduleEntry, deleteScheduleEntry, updateScheduleEntry } from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import { useToast } from "../../design-system/components/ToastContext";
import type { ScheduleEntry, WorkScheduleSuggestions } from "../../types";
import { formatDayHeading, shortTime } from "./scheduleDate";

interface ScheduleEntryModalProps {
  open: boolean;
  /** Дата дня, в котором нажали «+ Служебная запись». */
  entryDate: string;
  /** Строка для правки; без неё модал создаёт новую запись. */
  entry?: ScheduleEntry | null;
  suggestions: WorkScheduleSuggestions | null;
  onClose: () => void;
  onSaved: (mode: "created" | "updated") => void;
}

interface FormState {
  title: string;
  entry_date: string;
  time_from: string;
  time_to: string;
  organization: string;
  department: string;
  comment: string;
}

function initialState(entryDate: string, entry?: ScheduleEntry | null): FormState {
  return {
    title: entry?.title ?? "",
    entry_date: entry?.entry_date ?? entryDate,
    time_from: shortTime(entry?.time_from ?? null),
    time_to: shortTime(entry?.time_to ?? null),
    organization: entry?.organization ?? "",
    department: entry?.department ?? "",
    comment: entry?.comment ?? "",
  };
}

/**
 * Служебная строка графика без кандидата: «Увольнение 13:00–14:00», «перевод»,
 * «отработка грузчик», «медосмотр». Создание, правка и удаление доступны
 * пользователю с правами на график; каждое действие попадает в аудит.
 */
export function ScheduleEntryModal({
  open,
  entryDate,
  entry,
  suggestions,
  onClose,
  onSaved,
}: ScheduleEntryModalProps) {
  const { pushToast } = useToast();
  const [form, setForm] = useState<FormState>(() => initialState(entryDate, entry));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setForm(initialState(entryDate, entry));
      setError(null);
    }
  }, [open, entryDate, entry]);

  const save = async () => {
    if (!form.title.trim()) {
      setError("Укажите текст строки: увольнение, перевод, медосмотр…");
      return;
    }
    setBusy(true);
    setError(null);
    const payload = {
      entry_date: form.entry_date,
      time_from: form.time_from || null,
      time_to: form.time_to || null,
      title: form.title.trim(),
      organization: form.organization.trim() || null,
      department: form.department.trim() || null,
      comment: form.comment.trim() || null,
    };
    try {
      if (entry) {
        await updateScheduleEntry(entry.id, payload);
        pushToast("success", "Служебная строка обновлена.");
        onSaved("updated");
      } else {
        await createScheduleEntry(payload);
        pushToast("success", "Служебная строка добавлена в график.");
        onSaved("created");
      }
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось сохранить строку.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!entry) return;
    setDeleteOpen(false);
    try {
      await deleteScheduleEntry(entry.id);
      pushToast("success", "Служебная строка удалена.");
      onSaved("updated");
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось удалить строку.");
    }
  };

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title={entry ? "Служебная строка" : "Новая служебная запись"}
        description={`День: ${formatDayHeading(form.entry_date)}`}
        size="md"
        footer={
          <>
            {entry && (
              <Button variant="danger" onClick={() => setDeleteOpen(true)} disabled={busy}>
                Удалить
              </Button>
            )}
            <Button variant="secondary" onClick={onClose} disabled={busy}>
              Отмена
            </Button>
            <Button variant="primary" onClick={() => void save()} disabled={busy}>
              {entry ? "Сохранить" : "Добавить"}
            </Button>
          </>
        }
      >
        <form
          className="schedule-entry-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <Field label="Текст строки" required error={error ?? undefined}>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={form.title}
                placeholder="Увольнение, перевод, медосмотр…"
                invalid={Boolean(error)}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
              />
            )}
          </Field>
          <div className="schedule-entry-grid">
            <Field label="Дата">
              {(id) => (
                <TextInput
                  id={id}
                  type="date"
                  value={form.entry_date}
                  onChange={(event) => setForm({ ...form, entry_date: event.target.value })}
                />
              )}
            </Field>
            <Field label="Время с">
              {(id) => (
                <TextInput
                  id={id}
                  type="time"
                  value={form.time_from}
                  onChange={(event) => setForm({ ...form, time_from: event.target.value })}
                />
              )}
            </Field>
            <Field label="Время по" hint="Для интервала 13:00–14:00">
              {(id) => (
                <TextInput
                  id={id}
                  type="time"
                  value={form.time_to}
                  onChange={(event) => setForm({ ...form, time_to: event.target.value })}
                />
              )}
            </Field>
          </div>
          <div className="schedule-entry-grid">
            <Field label="Организация">
              {(id) => (
                <>
                  <TextInput
                    id={id}
                    list="schedule-organizations"
                    value={form.organization}
                    onChange={(event) => setForm({ ...form, organization: event.target.value })}
                  />
                </>
              )}
            </Field>
            <Field label="Отдел">
              {(id) => (
                <TextInput
                  id={id}
                  list="schedule-departments"
                  value={form.department}
                  onChange={(event) => setForm({ ...form, department: event.target.value })}
                />
              )}
            </Field>
          </div>
          <Field label="Комментарий">
            {(id) => (
              <TextInput
                id={id}
                value={form.comment}
                onChange={(event) => setForm({ ...form, comment: event.target.value })}
              />
            )}
          </Field>
          <datalist id="schedule-organizations">
            {(suggestions?.organizations ?? []).map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
          <datalist id="schedule-departments">
            {(suggestions?.departments ?? []).map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
        </form>
      </Modal>

      <ConfirmDialog
        open={deleteOpen}
        danger
        title="Удалить служебную строку?"
        description="Строка исчезнет из графика; действие записывается в аудит."
        confirmLabel="Удалить"
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => void remove()}
      />
    </>
  );
}
