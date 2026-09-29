import { useEffect, useState } from "react";
import { Button } from "../../design-system/components/Button";
import { Field, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import { toIsoDate, today } from "./scheduleDate";

interface StartDateModalProps {
  open: boolean;
  /** ФИО кандидата — окно открывается из Kanban при перетаскивании на «Вышел». */
  candidateName: string;
  /** Уже сохранённая дата (если есть) — подставляется по умолчанию. */
  initialDate?: string | null;
  busy?: boolean;
  error?: string | null;
  onCancel: () => void;
  /** `date` — ISO `YYYY-MM-DD`, `time` — `HH:MM` или null. */
  onConfirm: (date: string, time: string | null) => void;
}

/**
 * Маленькое окно «Укажите дату выхода»: сервер отказывает переводу в этап
 * «Вышел» без даты (422), поэтому Kanban и карточка спрашивают дату заранее.
 */
export function StartDateModal({
  open,
  candidateName,
  initialDate,
  busy,
  error,
  onCancel,
  onConfirm,
}: StartDateModalProps) {
  const [date, setDate] = useState(initialDate ?? toIsoDate(today()));
  const [time, setTime] = useState("");

  useEffect(() => {
    if (open) {
      setDate(initialDate ?? toIsoDate(today()));
      setTime("");
    }
  }, [open, initialDate]);

  const submit = () => {
    if (!date) return;
    onConfirm(date, time || null);
  };

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="Укажите дату выхода"
      description={`Кандидат «${candidateName}» переводится в этап «Вышел». Дата выхода попадёт в график.`}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Отмена
          </Button>
          <Button variant="primary" onClick={submit} disabled={busy || !date}>
            Перевести в «Вышел»
          </Button>
        </>
      }
    >
      <form
        className="start-date-form"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field label="Дата выхода" required error={error ?? undefined}>
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              type="date"
              value={date}
              invalid={Boolean(error)}
              onChange={(event) => setDate(event.target.value)}
            />
          )}
        </Field>
        <Field label="Время" hint="Необязательно">
          {(id) => (
            <TextInput
              id={id}
              type="time"
              value={time}
              onChange={(event) => setTime(event.target.value)}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
